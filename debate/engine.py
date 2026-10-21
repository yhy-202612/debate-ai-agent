"""编排出稿引擎：M1 四节点 + M3 提速。

三种模式（DEBATE_MODE）：
- one_shot   单次流式调用；先出稿、后补结构，首句延迟最低（推荐）
- two_stage  结构+策略（快模型）→ 并行多路出稿（强模型），鲁棒性优先
- spec       投机竞速：快/强两路并行出稿，先产出首句者独得全稿（推测解码思路）

M3 提速手段：固定 System 前缀（利于前缀缓存）、流式逐句推送（边写边出）、
分层用模（fast/strong）、并行子个体（two_stage）、投机竞速（spec）、
主备路由自动降级；并采集 token 用量（成本核算）与验收指标（metrics）。

事件流（async generator，供 SSE 转发）：
  {"type":"stage","stage":...,"status":"start"|"done","ms":int}
  {"type":"structured","data":{...}} / {"type":"plan","data":[...]}
  {"type":"sentence","idx":n,"text":...,"first":bool}
  {"type":"flag","text":...} / {"type":"warn","text":...} / {"type":"error","error":...}
  {"type":"done",...,"meta":{...}}
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator

from . import cost, factcheck, knowledge, materials, metrics, prompts
from .config import (CHOOSABLE_MODELS, choice_route, factcheck_enabled, load_routes,
                     max_tokens, mode, parallel_width)
from .models import ChatClient, ModelError

SPEECH_MARKER = "===SPEECH==="
ANALYSIS_MARKER = "===ANALYSIS==="

# ---------------------------------------------------------------- 工具函数

RISKY = re.compile(
    r"\d|[零一二三四五六七八九十百千万亿]+(?:%|％|成|倍|年|项|人|例)|"
    r"研究表明|数据显示|据统计|根据.{0,12}(?:研究|报告|调查)|"
    r"(?:哈佛|斯坦福|联合国|世界卫生组织).{0,14}(?:指出|证实|研究)"
)
_CJK = re.compile(r"[\u4e00-\u9fff]")


def parse_json(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for left, right in (("{", "}"), ("[", "]")):
        a, b = raw.find(left), raw.rfind(right)
        if a >= 0 and b > a:
            try:
                return json.loads(raw[a:b + 1])
            except json.JSONDecodeError:
                continue
    raise ModelError("模型输出不是合法 JSON")


_OPEN_AFTER = {"", "[", ",", ":", " ", "\n", "\r", "\t", "{"}
_CLOSE_BEFORE = {",", "]", "}", ":", " ", "\n", "\r", "\t"}


def extract_sentences(buf: str, force: bool = False) -> list[str]:
    """从（流式）缓冲中提取已闭合的、含中文的 JSON 字符串。

    - 仅当引号「前一个是 JSON 结构符」才算字符串开始、「后一个是结构符」才算结束，
      避免句子内部的英文引号（如 所谓的"进步"）导致误切、句尾被吞。
    - force=True（流已结束）时，允许把「下一字符尚未到达」的引号也视作结束，
      保证最后一句不会被漏掉。
    """
    out: list[str] = []
    in_str = esc = False
    start = 0
    for i, ch in enumerate(buf):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                nxt = buf[i + 1] if i + 1 < len(buf) else ""
                if nxt in _CLOSE_BEFORE or (force and nxt == ""):
                    seg = buf[start:i]
                    if _CJK.search(seg):
                        out.append(seg)
                    in_str = False
                # 否则：句内引号，继续
        elif ch == '"':
            prev = buf[i - 1] if i > 0 else ""
            if prev in _OPEN_AFTER:
                in_str = True
                start = i + 1
    return out


def risk_of(sentence: str):
    if RISKY.search(sentence):
        return f"「{sentence[:16]}…」涉及具体数字或无来源背书，已移除，需人工核对"
    if len(sentence) > 80:
        return f"「{sentence[:16]}…」过长，已移除"
    return ""


def parse_sentence_list(text: str) -> list[str]:
    try:
        obj = parse_json(text)
    except ModelError:
        return []
    if isinstance(obj, dict):
        obj = obj.get("sentences")
    if not isinstance(obj, list):
        return []
    return [str(s).strip() for s in obj if isinstance(s, str) and s.strip()]


def build_analysis(obj: Any) -> tuple[dict, list]:
    """校验并规整 {propositions, plan}。"""
    if not isinstance(obj, dict):
        raise ModelError("分析输出应为 JSON 对象")
    raw_props = obj.get("propositions")
    if not isinstance(raw_props, list) or not 1 <= len(raw_props) <= 3:
        raise ModelError("结构化输出应有 1-3 条主张")
    props = []
    for i, item in enumerate(raw_props, 1):
        if not isinstance(item, dict):
            raise ModelError("主张结构不正确")
        claim = str(item.get("claim") or "").strip()
        if not claim:
            raise ModelError("主张缺少内容")
        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        props.append({
            "id": f"P{i}",
            "claim": claim,
            "evidence": [str(e).strip() for e in evidence if str(e).strip()],
            "hidden_premise": str(item.get("hidden_premise") or "").strip(),
        })
    structured = {"propositions": props}
    ids = [p["id"] for p in props]
    plan: list[dict] = []
    raw_plan = obj.get("plan")
    if isinstance(raw_plan, list):
        for item in raw_plan:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "").strip()
            if target not in ids or target in [p["target"] for p in plan]:
                continue
            plan.append({
                "target": target,
                "angle": str(item.get("angle") or "").strip(),
                "line": str(item.get("line") or "").strip(),
            })
    return structured, plan


def side_note(side: str, topic: str = "", stance: str = "") -> str:
    """把「我方立场」显式化，避免模型误解。

    ★「A 还是 B」型辩题必须按赛制实际分配点名立场。2026-10-20 队内定稿：
      **反方 = 拓展方，正方 = 消解方**（此前相反，已按公布的赛制更正）。
      stance 显式传入时以 stance 为准，队别只作兜底。
    """
    s = (side or "").strip()
    t = topic or ""
    st = (stance or "").strip()
    if not st:
        if "消解" in s or "侵蚀" in s:
            st = "消解"
        elif "拓展" in s:
            st = "拓展"
        elif "正方" in s:
            st = "消解"
        elif "反方" in s:
            st = "拓展"
    dissolve = prompts.is_dissolve(st) if st else False
    if "消解" in t and "拓展" in t and st:
        ours = "消解" if dissolve else "拓展"
        other = "拓展" if dissolve else "消解"
        label = s or ("正方" if dissolve else "反方")
        return (f"{label}（我方立场={ours}；主张【人工智能的发展会{ours}人的价值】，"
                f"对方主张「{other}」）")
    if s == "正方":
        return "正方（支持辩题所陈述的观点）"
    if s == "反方":
        return "反方（反对辩题所陈述的观点）"
    return s or "正方（支持辩题所陈述的观点）"


# ── 我方队友已用素材检测：避免 AI 二辩与真人队友撞例子 ────────────────────────
# 转写里带「我方」的行 = 我方一辩/三辩已经说过的话。命中下列关键词即视为「已用过」，
# 交给模型排除，确保二辩的发言永远有信息增量。
MATERIAL_KEYS = ["AlphaFold", "两亿", "雨辰", "京东", "彭青云", "众神之战", "破亿",
                 "相机", "写实肖像", "织工", "蒸汽机", "计算器", "神笔马良", "马良", "工业革命"]


def used_materials(text: str) -> list[str]:
    """从场上转写中提取「我方队友已经用过的论据/案例」关键词。"""
    used: list[str] = []
    for line in (text or "").split("\n"):
        if "我方" not in line and "ours" not in line.lower():
            continue
        for k in MATERIAL_KEYS:
            if k in line and k not in used:
                used.append(k)
    return used


def _no_repeat_note(text: str) -> str:
    """拼一段「请勿重复队友素材」的提示（无命中时提示模型自行比对）。"""
    used = used_materials(text)
    if used:
        return ("\n\n【本场我方队友已用过的素材 · 请勿重复】" + "、".join(used) +
                "\n这些内容我方队友已经讲过，你再讲一遍就是浪费发言时间。"
                "请换其它论据，或换一个角度把队友的话往深里推（一句话带过即可）。")
    return ("\n\n【本场我方队友尚未使用论据库中的案例】\n你可以用论据库，但只用一个，"
            "把更多篇幅留给拆解对方的前提与追问。")


def _aug(prompt: str, memory: str = "", our_side: str = "", with_materials: bool = True,
         stance: str = "") -> str:
    """在提示词后追加「本场长期记忆」与辩论知识库（DEBATE_KB=off 时不追加）。"""
    parts = [prompt]
    mem = (memory or "").strip()
    if mem:
        parts.append(
            "【本场长期记忆（本场此前已说过的内容）】\n"
            "★硬规则：「我方已用论据」里出现过的例子、数据、类比、金句，本场**绝不再原样重复**；"
            "确需呼应时只能用一句话「接着往前推」，换角度切入，不得把例子重新讲一遍。\n" + mem)
    kb = knowledge.knowledge_block()
    if kb:
        parts.append(kb)
    # ★ 辩论队内资料（赛前讨论/质询训练/教练复盘提炼出的共识与红线），
    #   放在最后 = 权重最高：立场一致性与「包论」等红线优先于通用框架。
    mt = materials.materials_block(stance or our_side, allow=with_materials)
    if mt:
        parts.append(mt)
    return "\n\n".join(parts)


# ---------------- 长期总结（滚动摘要，A 方案）----------------
SUMMARY_SYSTEM = "你在为一场中文辩论做「长期记忆」摘要，只输出 JSON。"
SUMMARY_PROMPT = (
    "把【已有摘要】与【新增转写】合并成一份滚动摘要（已有摘要中的内容必须保留，除非被后续发言推翻），"
    "供我方二辩出稿时参考，以免重复已说过的内容、保持场上连贯。\n"
    "只返回 JSON，格式：\n"
    '{"our_points":["我方已提出的论点（去重、合并同类项，最多 8 条）"],'
    '"opp_points":["对方已提出的论点（去重，最多 8 条）"],'
    '"our_evidence":["我方已用过的关键论据/类比/例子（最多 5 条）"],'
    '"status":"一句话概括当前交锋态势（不超过 40 字）"}\n'
    "要求：\n"
    "①只依据给定文本，不编造、不引入新事实；\n"
    "②【重要】彻底合并去重：同一主张无论表述如何变化，在列表中只保留一条，"
    "采用最完整、最有力的表述；绝不允许两条含义相近的论点并列出现；\n"
    "③一律用简体中文，禁止夹带英文单词（如 merely、AI 之外的英文）；\n"
    "④总字数控制在 400 字以内；⑤若某类为空则返回空数组。\n"
    "【已有摘要】：\n{prev}\n【新增转写】：\n{recent}"
)



def _len_note(duration: int) -> str:
    """★字数硬约束：放在最终提示词**最末尾**（权重最高）。
    队内资料注入在末尾，模型容易被长材料带着写长，这里必须再钉一次。"""
    lo, hi = int(duration * 4.5), int(duration * 5.5)
    return (f"\n\n【字数硬约束 · 最高优先级，覆盖上面所有关于篇幅的说法】\n"
            f"本次发言只允许 {lo}-{hi} 字（约 {int(duration)} 秒口播）。"
            f"超过 {hi} 字直接判不合格，宁少不多。说到最后一句立刻停，不要补充、不要总结、不要凑时长。")

def _norm_point(s) -> str:
    """归一化：去掉标点/空白与常见虚词，便于模糊去重。"""
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", str(s or ""))
    return re.sub(r"(正在|已经|可以|能够|这个|那个|我们|他们|的|了|是)", "", t)


def _similar_point(a, b) -> bool:
    """判断两条论点是否实质重复（归一化相等 / 互相包含 / 字符集高重叠）。"""
    na, nb = _norm_point(a), _norm_point(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if min(len(na), len(nb)) >= 6 and (na in nb or nb in na):
        return True
    sa, sb = set(na), set(nb)
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.82


def merge_summary(prev: dict | None, new: dict, prefer_new: bool = False) -> dict:
    """合并摘要：模糊去重 + 限量。

    prefer_new=True（模型成功时）：以模型返回为准——模型已看过旧摘要并做过合并，
    再做一次模糊去重即可，避免「旧 ∪ 新」导致同一主张重复累积。
    prefer_new=False（模型失败时）：把新内容追加到旧摘要之后兜底。
    """
    def _take(primary, secondary, cap):
        out: list[str] = []
        for x in list(primary or []) + ([] if prefer_new else list(secondary or [])):
            t = str(x).strip()
            if t and not any(_similar_point(t, y) for y in out):
                out.append(t)
            if len(out) >= cap:
                break
        return out

    return {
        "our_points": _take(new.get("our_points"), (prev or {}).get("our_points"), 8),
        "opp_points": _take(new.get("opp_points"), (prev or {}).get("opp_points"), 8),
        "our_evidence": _take(new.get("our_evidence"), (prev or {}).get("our_evidence"), 5),
        "status": str(new.get("status") or (prev or {}).get("status") or "").strip()[:80],
    }


def summary_to_text(s: dict | None) -> str:
    """把结构化摘要压成一段纯文本，便于注入出稿提示词。"""
    if not s:
        return ""
    lines = []
    if s.get("status"):
        lines.append("当前态势：" + str(s["status"]))
    if s.get("our_points"):
        lines.append("我方已说：" + "；".join(str(x) for x in s["our_points"]))
    if s.get("opp_points"):
        lines.append("对方已说：" + "；".join(str(x) for x in s["opp_points"]))
    if s.get("our_evidence"):
        lines.append("我方已用论据：" + "；".join(str(x) for x in s["our_evidence"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- 引擎

class Engine:
    def __init__(self, routes=None):
        self.routes = routes or load_routes()
        self.fast = ChatClient(self.routes["fast"], max_tokens())
        self.strong = ChatClient(self.routes["strong"], max_tokens())
        backup_route = self.routes.get("backup")
        self.backup = ChatClient(backup_route, max_tokens()) if backup_route else None
        self.mode = mode()
        # 可切换的出稿模型（前端下拉），仅保留凭据就绪的
        self.choices: dict[str, ChatClient] = {}
        for m in CHOOSABLE_MODELS:
            r = choice_route(m["id"])
            if r and r.ready:
                # 推理型模型（deepseek）reasoning 消耗大，给更大的输出上限，避免 content 被截断为空
                mt = max(max_tokens(), 16000) if m["id"].startswith("deepseek-") else max_tokens()
                self.choices[m["id"]] = ChatClient(r, mt)

    def models_info(self) -> list[dict]:
        out = [{"id": "", "name": "默认（自动）", "ready": True, "group": ""}]
        for m in CHOOSABLE_MODELS:
            out.append({"id": m["id"], "name": m["name"],
                        "ready": m["id"] in self.choices, "group": m.get("group", "")})
        return out

    def client_for(self, model_id: str, slot: str = "rebut") -> ChatClient:
        """按槽位取客户端：出稿 rebut / 实时判定 judge / 长期总结 summary。

        model_id 为空或不可用时，回落该槽位的默认路由
        （出稿→strong、实时判定→fast、长期总结→strong）。
        """
        mid = (model_id or "").strip()
        if mid and mid in self.choices:
            return self.choices[mid]
        return {"rebut": self.strong, "judge": self.fast,
                "summary": self.strong}.get(slot, self.strong)

    def describe(self) -> dict:
        info = {
            "mode": self.mode,
            "fast": self.routes["fast"].describe(),
            "strong": self.routes["strong"].describe(),
            "parallel": parallel_width(),
            "backup": self.backup.route.describe() if self.backup else None,
        }
        return info

    async def rebut_stream(self, **kw) -> AsyncIterator[dict]:
        model_id = str(kw.pop("model", "") or "").strip()
        # ★ 本场我方立场（拓展/消解）：与队别解耦，2026-10-20 定稿「反方=拓展、正方=消解」
        stance = str(kw.pop("stance", "") or "").strip()
        if "our_side" in kw:
            kw["our_side"] = side_note(kw.get("our_side"), kw.get("topic") or "", stance)
        # 使用方自定义提示词（反方训练版等）：追加到提示词尾部，位置最靠后、权重最高
        extra = str(kw.pop("system_extra", "") or "").strip()
        if extra:
            base_mem = (kw.get("memory") or "").strip()
            kw["memory"] = (base_mem + "\n\n" if base_mem else "") + \
                "【使用方自定义提示词（务必遵循；与上面的默认风格或技法冲突时，以本段为准）】\n" + extra
        no_strategy = bool(kw.pop("no_strategy", False))
        # ★ 队内资料只供正方训练版与对话模式使用；反方版显式关掉
        with_materials = kw.pop("materials", True) is not False
        continuation = kw.pop("continuation", None) or None
        chunk_sec = int(kw.pop("chunk_sec", 0) or 0)
        strong = self.client_for(model_id, "rebut")
        runner = {
            "two_stage": self._rebut_two_stage,
            "spec": self._rebut_spec,
        }.get(self.mode, self._rebut_one_shot)
        # 续写 / 分段出稿只走 one_shot（结构+策略那套要先做整体分析，不适合接续）
        if continuation or chunk_sec:
            runner = self._rebut_one_shot
        usage = cost.UsageAcc()
        last_done: dict | None = None
        async for event in runner(usage=usage, strong_client=strong,
                                  no_strategy=no_strategy,
                                  with_materials=with_materials,
                                  stance=stance,
                                  continuation=continuation, chunk_sec=chunk_sec, **kw):
            if event.get("type") == "done":
                last_done = event      # 暂存，待事实核查后再下发
                continue
            yield event
        if last_done is not None:
            if factcheck_enabled() and last_done.get("sentences"):
                yield {"type": "stage", "stage": "factcheck", "status": "start"}
                await self._factcheck(last_done, usage)
                yield {"type": "stage", "stage": "factcheck", "status": "done", "ms": 0}
            meta = last_done.setdefault("meta", {})
            meta["model"] = strong.route.model
            meta["usage"] = usage.snapshot()
            try:
                metrics.record_run(mode=self.mode,
                                   first_ms=meta.get("first_sentence_ms"),
                                   sentences=len(last_done.get("sentences") or []),
                                   flags=len(last_done.get("flags") or []),
                                   usage=meta.get("usage"))
            except Exception:  # noqa: BLE001 指标记录绝不影响主流程
                pass
            yield last_done

    async def _factcheck(self, done: dict, usage: cost.UsageAcc) -> None:
        """出稿末尾事实核查：仅严重（具体数据/引用）剔除，轻微（过强断言）仅提示。"""
        sentences = done.get("sentences") or []
        if not sentences:
            return
        try:
            hits = await factcheck.check(self.fast, sentences, usage)
        except Exception:  # noqa: BLE001
            return
        meta = done.setdefault("meta", {})
        severe = {h["idx"] for h in hits if h.get("level") == "severe"}
        if severe:
            kept = [s for i, s in enumerate(sentences, 1) if i not in severe]
            done["sentences"] = kept
            done["speech"] = "\n".join(kept)
            meta["chars"] = sum(len(s) for s in kept)
        for h in hits:
            tag = "严重·已剔除" if h.get("level") == "severe" else "轻微"
            done.setdefault("flags", []).append(
                f"事实核查（{tag}）：第{h.get('idx')}句「{h.get('problem')}」（建议：{h.get('suggest')}）")
        meta["factcheck"] = {"sentences": len(sentences), "flagged": len(hits),
                             "dropped": len(severe), "hits": hits}

    def _finish(self, keep, flags, first_ms, t0, extra_meta, duration, usage=None):
        cap = duration * 6  # 按中文约 5.5 字/秒折算上限（实测 TTS 语速约 330 字/分）
        trimmed = 0
        while keep and sum(len(s) for s in keep) > cap and len(keep) > 2:
            keep.pop()
            trimmed += 1
        if trimmed:
            flags.append(f"为符合目标时长，已自动裁剪末尾 {trimmed} 句；可人工增补")
        length = sum(len(s) for s in keep)
        if keep and length < duration * 4.5:
            flags.append("稿件短于目标时长；请人工补足论证")
        if not keep:
            flags.append("没有可安全使用的句子；请人工重写或重试")
        meta = {"first_sentence_ms": first_ms, "chars": length,
                "routes": {"fast": self.routes["fast"].model,
                           "strong": self.routes["strong"].model}}
        meta.update(extra_meta)
        if usage is not None:
            meta["usage"] = usage.snapshot()
        return {"type": "done", "speech": "\n".join(keep), "sentences": keep,
                "flags": flags, "elapsed": round(time.monotonic() - t0, 2), "meta": meta}

    # ---------- 单次流式出稿（供主备/竞速复用）----------
    async def _stream_draft(self, client: ChatClient, prompt: str, t0: float,
                            out: dict, usage: cost.UsageAcc | None = None,
                            sys_prompt: str = "") -> AsyncIterator[dict]:
        whole = ""
        base = -1
        seen = 0
        struct_done = False
        keep: list[str] = []
        flags: list[str] = []
        first_ms = None
        try:
            async for piece in client.stream(
                    (sys_prompt or prompts.SYSTEM), prompt, temperature=0.4,
                    on_usage=(usage.add if usage else None)):
                whole += piece
                if base < 0:
                    cut = whole.find(SPEECH_MARKER)
                    if cut < 0:
                        continue
                    base = cut + len(SPEECH_MARKER)
                cut2 = whole.find(ANALYSIS_MARKER, base)
                seg = whole[base:cut2] if cut2 >= 0 else whole[base:]
                strings = extract_sentences(seg)
                while seen < len(strings):
                    sent = strings[seen].strip()
                    seen += 1
                    if not sent:
                        continue
                    problem = risk_of(sent)
                    if problem:
                        flags.append(problem)
                        yield {"type": "flag", "text": problem}
                    else:
                        keep.append(sent)
                        if first_ms is None:
                            first_ms = int((time.monotonic() - t0) * 1000)
                        yield {"type": "sentence", "idx": len(keep),
                               "text": sent, "first": len(keep) == 1}
                if not struct_done and cut2 >= 0:
                    try:
                        structured, plan = build_analysis(
                            parse_json(whole[cut2 + len(ANALYSIS_MARKER):]))
                    except ModelError:
                        continue
                    struct_done = True
                    yield {"type": "stage", "stage": "outline", "status": "start"}
                    yield {"type": "structured", "data": structured}
                    yield {"type": "plan", "data": plan}
                    yield {"type": "stage", "stage": "outline", "status": "done", "ms": 0}
        except ModelError as exc:
            yield {"type": "warn", "text": f"生成中断（{client.route.model}）：{exc}"}

        # 流结束时强制刷新：把末尾仍未闭合的句子也提取出来（防止漏最后一句）
        if base >= 0:
            try:
                cut2 = whole.find(ANALYSIS_MARKER, base)
                seg = whole[base:cut2] if cut2 >= 0 else whole[base:]
                strings = extract_sentences(seg, force=True)
                while seen < len(strings):
                    sent = strings[seen].strip()
                    seen += 1
                    if not sent:
                        continue
                    problem = risk_of(sent)
                    if problem:
                        flags.append(problem)
                        yield {"type": "flag", "text": problem}
                    else:
                        keep.append(sent)
                        if first_ms is None:
                            first_ms = int((time.monotonic() - t0) * 1000)
                        yield {"type": "sentence", "idx": len(keep),
                               "text": sent, "first": len(keep) == 1}
            except Exception:  # noqa: BLE001 刷新失败不影响主流程
                pass

        # 结构兜底（不影响稿件）
        if not struct_done:
            try:
                if base < 0:
                    obj = parse_json(whole)
                    structured, plan = build_analysis(obj)
                    for sent in parse_sentence_list(json.dumps(obj, ensure_ascii=False)):
                        problem = risk_of(sent)
                        if problem:
                            flags.append(problem)
                            yield {"type": "flag", "text": problem}
                        else:
                            keep.append(sent)
                            yield {"type": "sentence", "idx": len(keep), "text": sent,
                                   "first": len(keep) == 1}
                else:
                    cut2 = whole.find(ANALYSIS_MARKER)
                    tail = whole[cut2 + len(ANALYSIS_MARKER):] if cut2 >= 0 else ""
                    structured, plan = build_analysis(parse_json(tail))
                yield {"type": "stage", "stage": "outline", "status": "start"}
                yield {"type": "structured", "data": structured}
                yield {"type": "plan", "data": plan}
                yield {"type": "stage", "stage": "outline", "status": "done", "ms": 0}
            except ModelError as exc:
                yield {"type": "warn", "text": f"结构分析未生成（不影响稿件）：{exc}"}

        out.update({"keep": keep, "flags": flags, "first_ms": first_ms})

    # ================= 模式一：单次流式（先出稿、后补结构）=================
    async def _rebut_one_shot(self, *, opponent: str, our_side: str, our_case: str,
                              topic: str, duration: int, usage: cost.UsageAcc | None = None,
                              strong_client: ChatClient | None = None, memory: str = "",
                              no_strategy: bool = False,
                              with_materials: bool = True,
                              stance: str = "",
                              continuation: dict | None = None,
                              chunk_sec: int = 0
                              ) -> AsyncIterator[dict]:
        t0 = time.monotonic()
        strong = strong_client or self.strong
        # 纯净模式（训练版）：不注入辩论技法／考察口径，只留 TTS 对接约束
        sys_p = prompts.SYSTEM_CLEAN if no_strategy else prompts.system_for(stance or our_side)
        tpl = prompts.ONE_SHOT_CLEAN if no_strategy else prompts.ONE_SHOT
        yield {"type": "stage", "stage": "draft", "status": "start"}
        # 分段 / 续写：本段只负责「剩余时长」那一段，字数按剩余时长折算
        if continuation:
            eff_sec = max(6, int(continuation.get("remain_sec") or 20))
        else:
            eff_sec = max(6, min(int(duration) or 45, int(chunk_sec or 0) or int(duration) or 45))
        lo, hi = eff_sec * 4.5, eff_sec * 5.5
        prompt = _aug(prompts.render(
            tpl, duration=eff_sec, lo=lo, hi=hi,
            topic=topic, our_side=our_side, our_case=our_case, opponent=opponent), memory, our_side, with_materials, stance=stance) + _len_note(duration)
        prompt += _no_repeat_note(opponent)
        if not continuation and chunk_sec:
            prompt += prompts.CHUNK_NOTE.format(sec=eff_sec, lo=int(lo), hi=int(hi))
        if continuation:
            prompt += prompts.CONTINUE_NOTE.format(
                part=int(continuation.get("part") or 2),
                said=(str(continuation.get("said") or "").strip() or "（尚未开口）"),
                fresh=(str(continuation.get("fresh") or "").strip() or "（对方没有新的补充）"),
                remain=eff_sec, lo=int(lo), hi=int(hi))

        out: dict = {}
        async for event in self._stream_draft(strong, prompt, t0, out, usage, sys_p):
            yield event

        # 主模型无有效输出 → 备用模型自动降级
        if not out.get("keep") and self.backup is not None:
            yield {"type": "warn", "text": f"主模型无有效输出，切换备用模型"
                                           f"（{self.backup.route.model}）重试"}
            out = {}
            async for event in self._stream_draft(self.backup, prompt, t0, out, usage, sys_p):
                yield event

        yield {"type": "stage", "stage": "draft", "status": "done", "ms": 0}
        yield {"type": "stage", "stage": "guard", "status": "start"}
        yield {"type": "stage", "stage": "guard", "status": "done", "ms": 0}
        yield self._finish(out.get("keep") or [], out.get("flags") or [],
                           out.get("first_ms"), t0,
                           {"mode": "one_shot", "parallel": 1,
                            "part": int((continuation or {}).get("part") or 1)}, eff_sec, usage)

    # ================= 模式二：结构+策略 → 并行出稿 =================
    async def _rebut_two_stage(self, *, opponent: str, our_side: str, our_case: str,
                               topic: str, duration: int, usage: cost.UsageAcc | None = None,
                               strong_client: ChatClient | None = None, memory: str = "",
                               no_strategy: bool = False, with_materials: bool = True,
                               stance: str = "",
                               continuation: dict | None = None,
                               chunk_sec: int = 0
                               ) -> AsyncIterator[dict]:
        t0 = time.monotonic()
        sys_p = prompts.SYSTEM_CLEAN if no_strategy else prompts.system_for(stance or our_side)
        tpl = prompts.DRAFT_CLEAN if no_strategy else prompts.DRAFT
        width = parallel_width()
        strong = strong_client or self.strong
        aux_tasks: list[asyncio.Task] = []
        try:
            yield {"type": "stage", "stage": "outline", "status": "start"}
            s = time.monotonic()
            text = await self.fast.complete(
                sys_p,
                prompts.render(prompts.ANALYZE, opponent=opponent,
                               our_side=our_side, our_case=our_case),
                temperature=0.3,
                on_usage=(usage.add if usage else None))
            structured, plan = build_analysis(parse_json(text))
            yield {"type": "stage", "stage": "outline", "status": "done",
                   "ms": int((time.monotonic() - s) * 1000)}
            yield {"type": "structured", "data": structured}
            yield {"type": "plan", "data": plan}

            yield {"type": "stage", "stage": "draft", "status": "start"}
            draft_prompt = _aug(prompts.render(
                tpl, duration=duration, lo=duration * 4.5, hi=duration * 5.5,
                topic=topic, our_side=our_side, our_case=our_case, opponent=opponent,
                structured=json.dumps(structured, ensure_ascii=False),
                plan=json.dumps(plan, ensure_ascii=False)), memory, our_side, with_materials, stance=stance) + _len_note(duration)

            temps = [0.5, 0.85, 0.3, 1.0][:max(1, width)]
            aux_tasks = [asyncio.create_task(self._draft_once(draft_prompt, t, usage, strong))
                         for t in temps[1:]]

            keep: list[str] = []
            flags: list[str] = []
            first_ms = None
            buf = ""
            seen = 0
            try:
                async for piece in strong.stream(
                        sys_p, draft_prompt, temperature=temps[0],
                        on_usage=(usage.add if usage else None)):
                    buf += piece
                    strings = extract_sentences(buf)
                    while seen < len(strings):
                        sent = strings[seen].strip()
                        seen += 1
                        if not sent:
                            continue
                        problem = risk_of(sent)
                        if problem:
                            flags.append(problem)
                            yield {"type": "flag", "text": problem}
                        else:
                            keep.append(sent)
                            if first_ms is None:
                                first_ms = int((time.monotonic() - t0) * 1000)
                            yield {"type": "sentence", "idx": len(keep),
                                   "text": sent, "first": len(keep) == 1}
            except ModelError as exc:
                yield {"type": "warn", "text": f"主路生成失败：{exc}"}

            aux_results = await asyncio.gather(*aux_tasks, return_exceptions=True)
            if not keep:
                for res in aux_results:
                    if isinstance(res, list) and res:
                        for sent in res:
                            if not risk_of(sent):
                                keep.append(sent)
                                yield {"type": "sentence", "idx": len(keep),
                                       "text": sent, "first": len(keep) == 1}
                        if keep:
                            break

            yield {"type": "stage", "stage": "guard", "status": "start"}
            yield {"type": "stage", "stage": "guard", "status": "done", "ms": 0}
            aux_counts = [len(r) if isinstance(r, list) else -1 for r in aux_results]
            yield self._finish(keep, flags, first_ms, t0,
                               {"mode": "two_stage", "parallel": width,
                                "aux_sentence_counts": aux_counts}, duration, usage)
        except ModelError as exc:
            yield {"type": "error", "error": str(exc)}
        finally:
            for task in aux_tasks:
                if not task.done():
                    task.cancel()

    # ================= 模式三：投机竞速（推测解码思路）=================
    async def _rebut_spec(self, *, opponent: str, our_side: str, our_case: str,
                          topic: str, duration: int, usage: cost.UsageAcc | None = None,
                          strong_client: ChatClient | None = None, memory: str = "",
                          no_strategy: bool = False, with_materials: bool = True,
                          stance: str = "",
                          continuation: dict | None = None,
                          chunk_sec: int = 0
                          ) -> AsyncIterator[dict]:
        """快/强（+备用）多路并行出稿，先产出首句者独得全稿；其余路继续跑以备兜底。

        对应方案 M3 的「推测解码」：小/快模型抢跑首句以压低首字延迟，
        强模型并行兜底保证可用；谁先出声用谁，避免串行等待。
        """
        t0 = time.monotonic()
        strong = strong_client or self.strong
        yield {"type": "stage", "stage": "draft", "status": "start"}
        sys_p = prompts.SYSTEM_CLEAN if no_strategy else prompts.system_for(stance or our_side)
        tpl = prompts.ONE_SHOT_CLEAN if no_strategy else prompts.ONE_SHOT
        prompt = _aug(prompts.render(
            tpl, duration=duration, lo=duration * 4.5, hi=duration * 5.5,
            topic=topic, our_side=our_side, our_case=our_case, opponent=opponent), memory, our_side, with_materials, stance=stance) + _len_note(duration)

        # 仅快/强两路竞速；备用模型不参与（避免 3× 成本），仅在两路都失败时兜底
        lanes = [("strong", strong), ("fast", self.fast)]

        queue: asyncio.Queue = asyncio.Queue()
        outs: dict[str, dict] = {}

        async def lane(name: str, client: ChatClient) -> None:
            out: dict = {}
            outs[name] = out
            try:
                async for ev in self._stream_draft(client, prompt, t0, out, usage, sys_p):
                    await queue.put((name, "ev", ev))
                await queue.put((name, "end", None))
            except ModelError as exc:
                await queue.put((name, "err", str(exc)))

        tasks = [asyncio.create_task(lane(n, c)) for n, c in lanes]
        pending_ev: dict[str, list] = {}
        finished: set[str] = set()
        winner: str | None = None
        try:
            while len(finished) < len(lanes):
                name, kind, payload = await queue.get()
                if kind == "ev":
                    ev = payload
                    if winner is None:
                        if ev.get("type") == "sentence" and ev.get("first"):
                            winner = name
                            for e in pending_ev.pop(name, []):
                                yield e
                            yield {"type": "warn",
                                   "text": f"投机竞速：{name} 路先出首句（推测解码），采用该路全稿"}
                            yield ev
                        else:
                            pending_ev.setdefault(name, []).append(ev)
                    elif name == winner:
                        yield ev
                elif kind == "end":
                    finished.add(name)
                    if winner is None and (outs.get(name) or {}).get("keep"):
                        winner = name
                        for e in pending_ev.pop(name, []):
                            yield e
                        yield {"type": "warn", "text": f"投机竞速：{name} 路兜底（其余路无首句）"}
                else:  # err
                    finished.add(name)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        if winner is None:
            if self.backup is not None:
                yield {"type": "warn",
                       "text": f"竞速两路均未出稿，切换备用模型（{self.backup.route.model}）兜底"}
                out_b: dict = {}
                async for ev in self._stream_draft(self.backup, prompt, t0, out_b, usage):
                    yield ev
                outs["backup"] = out_b
                winner = "backup"
            else:
                yield {"type": "error", "error": "所有出稿路径均失败，请重试或改用 two_stage 模式"}
                return

        out = outs.get(winner) or {}
        ahead = None
        for name, o in outs.items():
            if name != winner and o.get("first_ms"):
                ahead = min(ahead, o["first_ms"]) if ahead else o["first_ms"]
        yield {"type": "stage", "stage": "draft", "status": "done", "ms": 0}
        yield {"type": "stage", "stage": "guard", "status": "start"}
        yield {"type": "stage", "stage": "guard", "status": "done", "ms": 0}
        yield self._finish(out.get("keep") or [], out.get("flags") or [],
                           out.get("first_ms"), t0,
                           {"mode": "spec", "race_winner": winner,
                            "lanes": list(outs.keys()),
                            "runner_up_first_ms": ahead}, duration, usage)

    async def _draft_once(self, prompt: str, temperature: float,
                          usage: cost.UsageAcc | None = None,
                          client: ChatClient | None = None) -> list[str]:
        text = await (client or self.strong).complete(prompts.SYSTEM, prompt, temperature=temperature,
                                                      on_usage=(usage.add if usage else None))
        return parse_sentence_list(text)

    # ---------- 长期总结（滚动摘要）----------
    async def summarize(self, *, prev=None, recent=None, model_id: str = "",
                        usage: cost.UsageAcc | None = None) -> dict:
        """把「已有摘要 + 新增转写」合并成滚动摘要（供出稿时注入长期记忆）。

        prev 可为 dict 或 None；recent 为转写文本列表。返回结构化摘要 dict。
        """
        recent = [str(r).strip() for r in (recent or []) if str(r).strip()]
        if not recent and not prev:
            return {"our_points": [], "opp_points": [], "our_evidence": [], "status": ""}
        client = self.client_for(model_id, "summary")
        prev_obj = prev if isinstance(prev, dict) else None
        prev_text = summary_to_text(prev_obj) if prev_obj else "（无）"
        prompt = prompts.render(SUMMARY_PROMPT, prev=prev_text, recent="\n".join(recent[-40:]))
        try:
            text = await client.complete(SUMMARY_SYSTEM, prompt, temperature=0.2,
                                         on_usage=(usage.add if usage else None))
            obj = parse_json(text)
        except ModelError:
            obj = None
        if not isinstance(obj, dict):
            # 模型失败：把新转写追加进旧摘要的对应列表（保底不丢信息）
            fallback = {"opp_points": recent[-6:]}
            return merge_summary(prev_obj, fallback, prefer_new=False)
        # 模型成功：以模型返回为准（它已看过旧摘要并合并过），再做模糊去重
        return merge_summary(prev_obj, obj, prefer_new=True)

    # ---------- P5 快问快答 ----------
    async def quick(self, *, question: str, our_side: str, our_case: str) -> dict:
        text = await self.fast.complete(
            prompts.render(prompts.QUICK, question=question, our_side=our_side, our_case=our_case),
            temperature=0.5)
        obj = parse_json(text)
        reply = str((obj or {}).get("reply") or "").strip() if isinstance(obj, dict) else ""
        if not reply:
            raise ModelError("应答为空")
        return {"reply": reply}

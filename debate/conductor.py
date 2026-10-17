"""现场指挥：身份归属（“谁在说”）与发言时机（“何时该我方说”）。

均为快模型 + 低温度的 JSON 判定，供全自动模式使用；失败时安全回退（不阻断）。
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .cost import UsageAcc
from .models import ChatClient, ModelError

ATTR_SYSTEM = "你在为中文辩论现场做说话人归属分析，只输出 JSON。"
ATTR_PROMPT = (
    "下面是辩论现场按时间顺序的若干段发言。【待判定段落】已按句切好、逐句编号。\n"
    "请判断【每一句】的来源身份：opp=对方辩手；ours=我方（含我方队员与 AI 二辩）；hall=主持人/评委/观众/其他。\n"
    "判断依据（按优先级从高到低）：\n"
    "1) 立场与观点：为正方或反方某一方辩护、或反驳某一方的，属于该方；结合「我方立场」判断——\n"
    "   与该立场一致的是 ours，与之对立的是 opp。\n"
    "2) 自称与他人称呼：「我方」「我」且语气为主场 = ours；「对方辩友」「贵方」多在 ours 口中出现；\n"
    "   称「我方」却又在为相反立场辩护时以【立场】为准。\n"
    "3) 上下文连续性：若本句与紧邻的上文（尤其最后一段）语气、主题直接衔接，像同一个人连续说下去，\n"
    "   则判为同一身份。\n"
    "4) 半句话也要判：一句话可能是没说完的半句或语气词，仍然要按「说话人是谁」来判，\n"
    "   不要因为句子不完整、太短或没有主语就改判成 hall 或另一身份。\n"
    "5) 只有确实是主持人串场（如「下面有请」「请双方」「本场辩论」「感谢X方X辩」等主持用语）才判 hall；\n"
    "   确实无法判断时才判 hall，不要瞎猜。\n"
    "6) 主持人串场常与紧接其后的辩手发言被切在同一段里，务必逐句区分开（例：\n"
    "   「感谢正方一辩，下面有请反方一辩进行立论陈词。谢谢主席。我方认为……」前两句是 hall、后一句是辨手）。\n"
    "只返回 JSON 数组，元素格式 {\"idx\":序号,\"sent\":[\"第1句的who\",\"第2句的who\",...]}；\n"
    "sent 的**长度必须与该段句数完全一致**、按句顺序给出，取值只能是 opp/ours/hall；\n"
    "**不要输出任何原文**（原文由系统自行拼接）。idx 为待判定段落的序号（从 1 开始）。\n"
    "我方立场：{side}\n辩题：{topic}\n【上文（仅供判断，不需判定）】：\n{context}\n"
    "【待判定段落（已按句编号）】：\n{items}"
)

SPEAK_SYSTEM = "你是辩论现场的 AI 二辩，判断现在是否轮到你发言。只输出 JSON。"
SPEAK_PROMPT = (
    "根据最近发言判断：现在是否轮到我方（AI 二辩）发言？\n"
    "只返回 JSON：{\"speak\":true|false,\"reason\":\"不超过 20 字\"}。\n"
    "判 false（从严，宁晚不抢）的典型情形：\n"
    "  ① 最近发言是我方（含我方队友、我方 AI）刚说完；\n"
    "  ② 对方仍在连续发言、话未告一段落（末句以逗号或「因为/所以/但是/首先/其次/最后」等收尾）；\n"
    "  ③ 主持人只是在开场、介绍、宣告规则、过渡流程，并未把话交给任何人；\n"
    "  ④ 主持人明确把发言权交给了对方（如「下面有请正方二辩」「请正方三辩提问」）。\n"
    "判 true 的情形：对方一段完整发言已结束、需要我方回应；或主持人点名邀请我方发言。\n"
    "我方立场：{side}\n最近发言：\n{recent}"
)

_WHO = {"opp", "ours", "hall"}

# 「主持人串场句」开头：用于把主席词从句中剥出来
_RE_CHAIR_HEAD = re.compile(r"^(感谢|谢谢|下面|接下来|有请|请|本场|首先|现在|时间到|进入|双方|尊敬的)")
# 出现这些词的句子即使以串场词开头，也按辩手话处理（避免误剥）
_RE_HOME_MARK = re.compile(r"我方认为|我方坚持|我方坚定|对方辩友|贵方|我们认为|我方主张")


def _norm_cn(s: str) -> str:
    """归一化：去掉空白与全部标点，只比较实义字符。"""
    return re.sub(r"[\s，。！？、；：“”‘’「」『』（）()【】《》…—\-·,.!?;:\"']+", "", s or "")


def _split_ok(parts: list, orig: str) -> bool:
    """校验模型给出的片内切分：拼起来必须与原文基本一致（防改写 / 丢内容 / 编造）。"""
    if not isinstance(parts, list) or len(parts) < 2:
        return False
    joined = _norm_cn("".join(str(p.get("text") or "") for p in parts))
    src = _norm_cn(orig)
    if not joined or not src:
        return False
    if not (0.75 <= len(joined) / len(src) <= 1.30):
        return False
    ca, cb = Counter(joined), Counter(src)
    inter = sum((ca & cb).values())
    return inter / max(len(src), 1) >= 0.90


def _split_parts(orig: str) -> list[dict]:
    """规则兜底切分：把「主持人串场句」从句首/句中剥出来。

    返回 [{"who": "hall"|"", "text": ...}]；who 为空表示「非主席部分」，
    由调用方回填整体判定结果。切不出（或切完只剩一段）时返回 []。
    """
    t = (orig or "").strip()
    if not t:
        return []
    chunks = [c for c in re.split(r"(?<=[。！？…])", t) if c.strip()]
    if len(chunks) < 2:
        return []
    parts: list[dict] = []
    buf, buf_chair = "", None
    for c in chunks:
        chair = bool(_RE_CHAIR_HEAD.match(c.strip())) and not _RE_HOME_MARK.search(c)
        if buf and chair == buf_chair:
            buf += c
        else:
            if buf:
                parts.append({"who": "hall" if buf_chair else "", "text": buf})
            buf, buf_chair = c, chair
    if buf:
        parts.append({"who": "hall" if buf_chair else "", "text": buf})
    if len(parts) < 2:
        return []
    return parts


def _loads(raw: str):
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for a, b in (("[", "]"), ("{", "}")):
        i, j = s.find(a), s.rfind(b)
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None


def _sent_split(t: str) -> list[str]:
    """按中文句末标点切句（保留标点），过滤空白。用于「逐句归属」的对齐基准。"""
    parts = re.split(r"(?<=[。！？…；;!?])", t or "")
    return [p for p in parts if p and p.strip()]


async def attribute(client: ChatClient, texts: list[str], our_side: str = "",
                    topic: str = "", context: list[str] | None = None,
                    usage: UsageAcc | None = None, split: bool = True) -> list[dict]:
    """判定每段发言的来源身份，返回 [{idx, who, text, parts?}]（未判定的回退 hall）。

    split=True 时，若某段内混有「主持人串场 + 辩手发言」，会按句归属拆成 parts。
    ★ 实现要点：只让模型回「每句的 who 序列」，原文由本地按句拼回——
      这样输出极短（快）、且模型无法改写原文（安全）。
    """
    texts = [t for t in (texts or []) if t and t.strip()]
    if not texts:
        return []
    sents_per = [_sent_split(t) for t in texts]
    ctx = "\n".join(f"- {c}" for c in (context or [])[-6:]) or "（无）"
    lines: list[str] = []
    for i, ss in enumerate(sents_per, 1):
        lines.append(f"【段 {i}】共 {len(ss)} 句：")
        for j, s in enumerate(ss, 1):
            lines.append(f"  {i}.{j} {s}")
    items = "\n".join(lines)
    prompt = (ATTR_PROMPT.replace("{side}", our_side or "未指定")
              .replace("{topic}", topic or "未指定")
              .replace("{context}", ctx).replace("{items}", items))
    try:
        raw = await client.complete(ATTR_SYSTEM, prompt, temperature=0.0,
                                    on_usage=(usage.add if usage else None))
    except ModelError:
        raw = ""
    data = _loads(raw)
    who_map: dict[int, str] = {}
    sent_map: dict[int, list[str]] = {}
    part_map: dict[int, list] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("idx"))
            except (TypeError, ValueError):
                continue
            if not (1 <= idx <= len(texts)):
                continue
            who = str(item.get("who") or "").strip().lower()
            # ① 逐句归属序列（主路径）
            sent = item.get("sent")
            if isinstance(sent, list) and sents_per and len(sent) == len(sents_per[idx - 1]):
                seq = [(str(x).strip().lower() if str(x).strip().lower() in _WHO else "hall")
                       for x in sent]
                sent_map[idx] = seq
                cnt = Counter(w for w in seq if w != "hall")   # 整体归属 = 出现最多的非会场身份
                who = cnt.most_common(1)[0][0] if cnt else "hall"
            # ② 兼容：模型直接给 parts
            parts = item.get("parts")
            if isinstance(parts, list) and len(parts) >= 2:
                norm_parts = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    pw = str(p.get("who") or "").strip().lower()
                    if pw not in _WHO:
                        pw = "hall"
                    norm_parts.append({"who": pw, "text": str(p.get("text") or "")})
                if len(norm_parts) >= 2:
                    part_map[idx] = norm_parts
                    non_hall = [p["who"] for p in norm_parts if p["who"] != "hall"]
                    who = non_hall[0] if non_hall else "hall"
            if who not in _WHO:
                who = "hall"
            who_map[idx] = who
    out: list[dict] = []
    for i, t in enumerate(texts, 1):
        who = who_map.get(i, "hall")
        rec: dict = {"idx": i, "who": who, "text": t}
        if split:
            sents = sents_per[i - 1] if i - 1 < len(sents_per) else []
            seq = sent_map.get(i)
            if seq and sents and len(seq) == len(sents):        # 本地按句拼回（不会改写原文）
                merged: list[dict] = []
                for w, s in zip(seq, sents):
                    if merged and merged[-1]["who"] == w:
                        merged[-1]["text"] += s
                    else:
                        merged.append({"who": w, "text": s})
                if len(merged) > 1:
                    rec["parts"] = merged
            if "parts" not in rec:
                parts = part_map.get(i)
                if parts and _split_ok(parts, t):
                    rec["parts"] = parts
                else:
                    rp = _split_parts(t)                        # 规则兜底：模型没切或切坏了
                    if rp:
                        for p in rp:
                            if not p["who"]:
                                p["who"] = who
                        rec["parts"] = rp
        out.append(rec)
    return out


_RE_INVITE = re.compile(r"有请|下面由|下面请|轮到|接下来|请")
# 邀请动词 + 紧跟其后的姓名（必须相邻才算「点名」，避免把「感谢X。下面有请Y」误当成点名 X）
_RE_CALL = r"(?:有请|请|下面由|下面请|轮到|接下来请|接下来由)\s*"
_RE_SELF = re.compile(r"(?:有请|请|下面由|下面请|轮到|接下来请|接下来由)\s*(?:我方|我队|我队辩手)")


def _our_side_word(our_side: str) -> str:
    s = our_side or ""
    if "反" in s:
        return "反方"
    if "正" in s:
        return "正方"
    return ""


def chair_calls_us(text: str, our_side: str) -> bool:
    """主持人是否在点名邀请「我方（AI 二辩）」发言。

    要点：点名必须「邀请动词 + 紧跟的称呼」相邻才算（例：「有请正方二辩」）。
    仅出现「谢谢/感谢 X 方二辩」不算；「感谢正方二辩。下面有请反方二辩」式句子，
    必须判为【在请对方】，不能因为句中出现我方称呼就命中。
    """
    t = text or ""
    if not _RE_INVITE.search(t):
        return False
    if _RE_SELF.search(t):
        return True
    me = _our_side_word(our_side)
    if me and re.search(_RE_CALL + me + r"\s*二辩", t):
        return True
    return False


def chair_calls_other(text: str, our_side: str) -> bool:
    """主持人是否在明确引导对方发言（此时我方不应插话）。"""
    t = text or ""
    if not _RE_INVITE.search(t):
        return False
    me = _our_side_word(our_side)
    other = "正方" if me == "反方" else ("反方" if me == "正方" else "")
    if not other:
        return False
    return bool(re.search(_RE_CALL + other + r"\s*[一二三四]?\s*辩", t))


# 「列举开启」词 / 「列举收尾」词：用于识别「对方只说了『首先…。』、还没说『其次/最后』」这种
# 看似收束、实则话没说完的情况——规则快通道必须把它排除，否则会抢话。
_ENUM_HEAD = ("首先", "其次", "第一", "第二", "第三", "第四", "其一", "其二", "一是", "二是")
_ENUM_CLOSE = ("最后", "综上", "总之", "总结", "因此", "所以", "以上", "归结", "一句话", "概括")


def _enum_unfinished(t: str) -> bool:
    """※只看「最后一句」：若整段的末句仍停在列举开启处（以『首先/其次/第一，』等开头），
    说明对方正在往下数、话没说完 → 返回 True。若已有『最后/综上/因此』类收尾词，则视为说完。

    注意：不能用「整段含列举词」来判——辩论里『第一…第二…第三…』是常态，
    那样会把几乎所有发言都拦下，快通道永远命中不了。"""
    t = (t or "").strip()
    if not t:
        return False
    if any(c in t for c in _ENUM_CLOSE):
        return False
    parts = _sent_split(t)
    last = (parts[-1] if parts else t).strip()
    return any(last.startswith(o) for o in _ENUM_HEAD)


async def should_speak(client: ChatClient, recent: list[str], our_side: str = "",
                       gap_ms: int | None = None, tail_complete: bool | None = None,
                       usage: UsageAcc | None = None) -> dict:
    """判断当前是否该我方发言，返回 {"speak": bool, "reason": str}。

    从严策略：静默时长不足「强静默阈值」时，一律不发言——避免对方话说到一半（中途换气/
    停顿）被误判为「已讲完」而抢话。tail_complete 为 False 时同样直接不发言。
    """
    recent = [str(r) for r in (recent or []) if str(r).strip()]
    if not recent:
        return {"speak": False, "reason": "无发言"}

    STRONG_GAP_MS = 4000          # 强静默阈值：达到即认为对方一段话已讲完
    MIN_GAP_MS = 1200             # 最低静默（末句未收束时）：低于此值一律不发言（防抢话）
    FAST_GAP_MS = 800             # ★末句已收束时的快速门槛（语义判停 EOU，借鉴豆包实时对话）

    # ── 规则一（最高优先，不依赖模型）：主持人点名 → 立即发声或明确让行 ──
    # 真实赛场上主席那句「下面有请反方二辩进行反驳」就是发令枪；它出现时静默往往很短，
    # 若仍按静默门槛卡，就会永远等不到发言机会。
    if chair_calls_us(recent[-1], our_side):
        return {"speak": True, "reason": "主持人邀请我方发言"}
    if chair_calls_other(recent[-1], our_side):
        return {"speak": False, "reason": "主持人在引导对方发言"}

    # ── 硬门槛（不依赖模型）：话音刚停 或 末句明确未收束 → 不发言 ──
    # ★语义判停（EOU）：末句已收束（tail_complete=True）说明一句话讲完了，静默只需很短即可判定；
    #   未收束时仍用较高门槛防抢话。以「语义完整性」为主、静默时长为辅（借鉴豆包 Seeduplex）。
    min_gap = FAST_GAP_MS if tail_complete else MIN_GAP_MS
    if gap_ms is not None and gap_ms < min_gap:
        return {"speak": False, "reason": f"静默仅 {gap_ms/1000:.1f}s，话音刚停"}
    if tail_complete is False and (gap_ms is None or gap_ms < STRONG_GAP_MS):
        return {"speak": False, "reason": "末句未收束"}

    # ── 规则二（不依赖模型）：★语义判停快通道（借鉴豆包 Seeduplex「判停不走大模型」）──
    #   末句已收束 + 静默达保险值 + 对方整段够长 + 不是「列举未完」→ 直接判定说完了，
    #   省掉约 2 秒的模型判定耗时。条件刻意保守，宁可多等也不误抢。
    RULE_FAST_GAP_MS = 1500       # 快通道静默保险值（高于语义门槛 0.8s，留出换气余量）
    RULE_MIN_CHARS = 30           # 对方整段累计最少字数
    if (tail_complete and gap_ms is not None and gap_ms >= RULE_FAST_GAP_MS
            and len("".join(recent)) >= RULE_MIN_CHARS
            and not _enum_unfinished(recent[-1])):
        return {"speak": True, "reason": "对方末句已收束，快速接话"}

    text = "\n".join(f"- {r}" for r in recent[-8:])
    gap_desc = ("约 %d 秒" % round((gap_ms or 0) / 1000)) if gap_ms is not None else "未知"
    tail_desc = "是" if tail_complete else ("否" if tail_complete is False else "未知")
    prompt = (SPEAK_PROMPT.replace("{side}", our_side or "未指定")
              .replace("{gap}", gap_desc).replace("{tail}", tail_desc)
              .replace("{recent}", text))
    try:
        raw = await client.complete(SPEAK_SYSTEM, prompt, temperature=0.0,
                                    on_usage=(usage.add if usage else None))
    except ModelError:
        return {"speak": False, "reason": "判断失败"}
    data = _loads(raw)
    if isinstance(data, dict):
        return {"speak": bool(data.get("speak")), "reason": str(data.get("reason") or "")[:40]}
    return {"speak": False, "reason": "解析失败"}

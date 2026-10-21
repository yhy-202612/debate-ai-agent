"""对话模式（正方版）：端到端 / 级联对话 + 讨论总结。

与「辩论出稿」的区别：
- 不做立场对抗，只顺着对方说的聊
- 输出短（1~3 句，25~80 字），像面对面交流
- 「是否主动插话」由前端开关控制，后端只负责回应一次
- 对话中沉淀「资料（notes）」与「偏好（prefs）」，下一轮喂回去

SSE 事件（与辩论文档一致，前端可复用同一套解析）：
  {"type":"text","delta":str}     字幕增量
  {"type":"audio","delta":str}    base64 PCM16 / 24kHz（仅端到端通道）
  {"type":"done","text":str,...}
  {"type":"error","message":str}
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import AsyncIterator

from . import materials
from .config import dashscope_key
from .omni import (RT_URL, SAMPLE_RATE, CONNECT_TIMEOUT, IDLE_TIMEOUT,
                   AUDIO_FLUSH_BYTES, _header_kwarg, model_id, voice_id)

CHAT_SYSTEM = """你是一个讨论伙伴，正在和同学面对面聊一个话题。

说话要求（很重要）：
- 像真人说话：1~3 句，总共 25~80 字，不要长篇大论
- 先接住对方说的关键点，再给一句自己的看法或追问
- 不要「好的」「下面我来」「综上所述」这类过场话
- 不要 Markdown、不要分点、不要括号旁白
- 不复述对方原话；有资料时优先用资料里的说法，没资料就不要编数字
"""

SUMMARY_SYSTEM = """你在整理一段讨论记录，只输出 JSON（不要 Markdown 代码块，不要解释）。

格式：
{
  "notes":   [{"title": "≤12字", "content": "≤80字"}],
  "prefs":   ["≤40字"],
  "topics":  ["≤16字"],
  "summary": "≤150字"
}

含义：
- notes   = 讨论里出现过的事实、数据、观点、例子（以后还能复用）
- prefs   = 从用户表达里观察到的偏好：关心什么角度、爱举什么例子、说话风格
- topics  = 讨论到的子话题
- summary = 这段讨论的整体概括

要求：
- notes 最多 8 条、prefs 最多 5 条、topics 最多 6 条
- 与「已有记忆」重复的不要重复列，除非有新信息
- 只保留有信息量的内容；不确定的不要编
"""


def _norm_messages(messages, limit: int = 14) -> list[dict]:
    out: list[dict] = []
    for m in (messages or [])[-limit:]:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        out.append({"role": "assistant" if role in ("assistant", "ai") else "user",
                    "content": content[:1500]})
    return out


# 供 api 层调用
norm_messages = _norm_messages


def _memory_block(notes, prefs, topics) -> str:
    """把记忆压成几行文本，喂给模型（控制长度）。"""
    lines: list[str] = []
    ns = [n for n in (notes or []) if isinstance(n, dict) and n.get("content")][:8]
    if ns:
        lines.append("【已整理的资料】")
        for n in ns:
            t = str(n.get("title") or "").strip()
            c = str(n.get("content") or "").strip()
            lines.append(f"- {t}：{c}" if t else f"- {c}")
    ps = [str(p).strip() for p in (prefs or []) if str(p).strip()][:5]
    if ps:
        lines.append("【对方的偏好】")
        lines += [f"- {p}" for p in ps]
    ts = [str(t).strip() for t in (topics or []) if str(t).strip()][:6]
    if ts:
        lines.append("【讨论到的话题】" + "、".join(ts))
    return "\n".join(lines)


def _dialog_text(messages, topic: str = "") -> str:
    lines: list[str] = []
    if topic:
        lines.append(f"【讨论主题】{topic}")
    lines.append("【最近对话】")
    for m in messages:
        who = "你" if m["role"] == "assistant" else "对方"
        lines.append(f"{who}：{m['content']}")
    return "\n".join(lines)


def _build_task(*, messages, topic, notes, prefs, topics,
                unsummarized=None, use_full=False, use_memory=True,
                use_distill=False, distill="") -> str:
    """拼「本次回应的上下文」。

    上下文来源是可勾选的（前端三个复选框）：
      ① 未总结段原文（use_full）——逐字保留，防止 AI 只看摘要丢细节
      ② 已总结记忆  （use_memory）
      ③ 人工蒸馏结果（use_distill）
    """
    parts: list[str] = []
    if topic:
        parts.append(f"【讨论主题】{topic}")

    if use_full and unsummarized:
        parts += ["", "【尚未总结的对话原文（逐字保留，可按需引用细节）】",
                  _dialog_text(unsummarized[:24], "")]

    if use_memory:
        mem = _memory_block(notes, prefs, topics)
        if mem:
            parts += ["", mem]

    if use_distill and (distill or "").strip():
        parts += ["", "【人工蒸馏结果（优先采用，冲突时以本段为准）】", distill.strip()[:6000]]

    parts += ["", _dialog_text(messages, "")]
    parts += ["", "【现在】请用 1~3 句口语，直接回应最后一句。不要复述。"]
    return "\n".join(parts)


def _build_instructions(*, topic, notes, prefs, topics, extra,
                        use_memory: bool = True, use_distill: bool = False,
                        distill: str = "", with_materials: bool = True) -> str:
    parts = [CHAT_SYSTEM.strip()]
    if topic:
        parts += ["", f"【讨论主题】{topic}"]
    if use_memory:
        mem = _memory_block(notes, prefs, topics)
        if mem:
            parts += ["", mem]
    if use_distill and (distill or "").strip():
        parts += ["", "【人工蒸馏结果（优先采用）】", distill.strip()[:4000]]
    ext = (extra or "").strip()
    if ext:
        parts += ["", "【使用方自定义提示词（务必遵循）】", ext]
    mt = materials.materials_block(allow=with_materials)   # 队内共识（对话模式启用）
    if mt:
        parts += ["", mt]
    return "\n".join(parts)


def _cascade_prompt(*, messages, topic, notes, prefs, topics, extra,
                    unsummarized=None, use_full=False, use_memory=True,
                    use_distill=False, distill="", with_materials=True) -> tuple[str, str]:
    system = _build_instructions(topic=topic, notes=notes, prefs=prefs, topics=topics,
                                 extra=extra, use_memory=use_memory,
                                 use_distill=use_distill, distill=distill,
                                 with_materials=with_materials)
    user = _build_task(messages=messages, topic="", notes=[], prefs=[], topics=[],
                       unsummarized=unsummarized, use_full=use_full,
                       use_memory=False, use_distill=False)
    return system, user


# ---------------- 端到端（Omni）----------------

async def chat_stream_omni(*, messages, topic: str = "", notes=None, prefs=None,
                           topics=None, voice: str = "", model: str = "",
                           extra: str = "", unsummarized=None, use_full: bool = False,
                           use_memory: bool = True, use_distill: bool = False,
                           distill: str = "", with_materials: bool = True) -> AsyncIterator[dict]:
    key = dashscope_key()
    if not key:
        yield {"type": "error", "message": "未配置 DASHSCOPE_API_KEY，端到端通道不可用"}
        return
    try:
        import websockets  # noqa: F401
    except Exception:
        yield {"type": "error", "message": "服务器缺少 websockets 依赖"}
        return

    msgs = _norm_messages(messages)
    if not msgs:
        yield {"type": "error", "message": "没有可回应的内容"}
        return

    # ★ 端到端只认 omni 模型：前端把「级联对话模型」（如 glm-5.3）一起带过来时，
    #   直接用它当 omni 模型会报 Model not found，这里做一次兜底纠正。
    mdl = (model or "").strip()
    if "omni" not in mdl.lower():
        mdl = model_id()
    voc = (voice or voice_id()).strip()
    full_msgs = _norm_messages(unsummarized, limit=24) if use_full else None
    instructions = _build_instructions(topic=topic, notes=notes, prefs=prefs,
                                       topics=topics, extra=extra,
                                       use_memory=use_memory, use_distill=use_distill,
                                       distill=distill, with_materials=with_materials)
    task = _build_task(messages=msgs, topic=topic, notes=notes, prefs=prefs, topics=topics,
                       unsummarized=full_msgs, use_full=use_full,
                       use_memory=use_memory, use_distill=use_distill, distill=distill)
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Beta": "realtime=v1",
               "User-Agent": "Mozilla/5.0"}

    t0 = time.time()
    ws = None
    try:
        ws = await asyncio.wait_for(
            websockets.connect(f"{RT_URL}?model={mdl}", max_size=None,
                               **{_header_kwarg(): headers}), timeout=CONNECT_TIMEOUT)
        await asyncio.wait_for(ws.recv(), timeout=10)          # session.created
        await ws.send(json.dumps({"type": "session.update", "session": {
            "modalities": ["text", "audio"],
            "voice": voc,
            "instructions": instructions,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": None,
        }}, ensure_ascii=False))
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if ev.get("type") == "session.updated":
                break
            if ev.get("type") == "error":
                yield {"type": "error", "message": "会话初始化失败：" + json.dumps(ev, ensure_ascii=False)[:300]}
                return

        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": task}]}}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "response.create",
                                  "response": {"modalities": ["text", "audio"]}}))

        buf = bytearray()
        text_parts: list[str] = []
        first_text_ms = None
        first_audio_ms = None
        last_emit = time.time()
        emitted = False

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                yield {"type": "error", "message": "端到端通道长时间无响应"}
                break
            ev = json.loads(raw)
            et = ev.get("type", "")
            if et in ("response.audio_transcript.delta", "response.text.delta"):
                d = ev.get("delta") or ""
                if d:
                    if first_text_ms is None:
                        first_text_ms = int((time.time() - t0) * 1000)
                    text_parts.append(d)
                    yield {"type": "text", "delta": d}
            elif et == "response.audio.delta":
                piece = base64.b64decode(ev.get("delta") or "")
                if piece:
                    if first_audio_ms is None:
                        first_audio_ms = int((time.time() - t0) * 1000)
                    buf.extend(piece)
                    if not emitted or len(buf) >= AUDIO_FLUSH_BYTES or time.time() - last_emit >= 0.2:
                        chunk = bytes(buf)
                        buf = bytearray()
                        last_emit = time.time()
                        emitted = True
                        yield {"type": "audio", "delta": base64.b64encode(chunk).decode()}
            elif et in ("response.done", "response.completed"):
                break
            elif et == "error":
                yield {"type": "error", "message": "端到端通道报错：" + json.dumps(ev, ensure_ascii=False)[:300]}
                break

        if buf:
            yield {"type": "audio", "delta": base64.b64encode(bytes(buf)).decode()}
        text = "".join(text_parts).strip()
        yield {"type": "done", "text": text, "voice": voc, "model": mdl,
               "sample_rate": SAMPLE_RATE,
               "first_text_ms": first_text_ms, "first_audio_ms": first_audio_ms,
               "ms": int((time.time() - t0) * 1000), "chars": len(text)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"端到端通道不可用：{type(exc).__name__}: {exc}"[:300]}
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


# ---------------- 级联（GLM-5.3 文本）----------------

async def chat_stream_cascade(*, client, messages, topic: str = "", notes=None,
                              prefs=None, topics=None, extra: str = "",
                              unsummarized=None, use_full: bool = False,
                              use_memory: bool = True, use_distill: bool = False,
                              distill: str = "", with_materials: bool = True) -> AsyncIterator[dict]:
    msgs = _norm_messages(messages)
    if not msgs:
        yield {"type": "error", "message": "没有可回应的内容"}
        return
    system, user = _cascade_prompt(messages=msgs, topic=topic, notes=notes,
                                   prefs=prefs, topics=topics, extra=extra,
                                   unsummarized=_norm_messages(unsummarized, limit=24),
                                   use_full=use_full, use_memory=use_memory,
                                   use_distill=use_distill, distill=distill,
                                   with_materials=with_materials)
    t0 = time.time()
    first_ms = None
    parts: list[str] = []
    try:
        async for delta in client.stream(system, user, temperature=0.7, max_tokens=600):
            d = str(delta or "")
            if not d:
                continue
            if first_ms is None:
                first_ms = int((time.time() - t0) * 1000)
            parts.append(d)
            yield {"type": "text", "delta": d}
        text = "".join(parts).strip()
        yield {"type": "done", "text": text, "model": getattr(client, "name", ""),
               "first_text_ms": first_ms, "ms": int((time.time() - t0) * 1000),
               "chars": len(text), "sample_rate": 0}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"对话生成失败：{type(exc).__name__}: {exc}"[:300]}


# ---------------- 讨论总结 ----------------

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _loose_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    m = _JSON_RE.search(s)
    if m:
        s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        return {}


def _dedup_notes(prev, new) -> list[dict]:
    """按 title/内容相似度合并，避免同一观点反复累积。"""
    def norm(t: str) -> str:
        return re.sub(r"[\s，。、；：,.;:!?！？「」“”\"'()（）]", "", str(t or ""))

    out: list[dict] = [dict(n) for n in (prev or []) if isinstance(n, dict) and n.get("content")]
    for n in (new or []):
        if not isinstance(n, dict) or not n.get("content"):
            continue
        key = norm(n.get("title") or n.get("content"))[:24]
        if not key:
            continue
        dup = False
        for o in out:
            ok = norm(o.get("title") or o.get("content"))[:24]
            if ok == key or (len(ok) > 6 and (ok in key or key in ok)):
                dup = True
                # 新内容更长则替换
                if len(str(n.get("content"))) > len(str(o.get("content"))):
                    o.update(n)
                break
        if not dup:
            out.append(dict(n))
    return out[:8]


def _dedup_list(prev, new, limit: int = 5) -> list[str]:
    out = [str(x).strip() for x in (prev or []) if str(x).strip()]
    for x in (new or []):
        s = str(x).strip()
        if not s:
            continue
        if any(s in o or o in s for o in out):
            continue
        out.append(s)
    return out[-limit:]


async def summarize_discussion(*, client, messages, prev=None) -> dict:
    msgs = _norm_messages(messages, limit=40)
    prev = prev if isinstance(prev, dict) else {}
    prev_block = _memory_block(prev.get("notes"), prev.get("prefs"), prev.get("topics"))
    user = _dialog_text(msgs, "") + ("\n\n" + prev_block if prev_block else "")
    raw = await client.complete(SUMMARY_SYSTEM, user[:12000], temperature=0.2, max_tokens=4000)
    data = _loose_json(raw if isinstance(raw, str) else str(raw))
    if not data:
        # ★ 推理模型（glm-5.3 / deepseek 系列）的 reasoning 会吃掉额度，
        #   max_tokens 太小会导致 content 为空、整段总结全空（实测 1200 必空、4000 正常）。
        #   这里再放大一次重试兜底。
        raw = await client.complete(SUMMARY_SYSTEM, user[:12000], temperature=0.2, max_tokens=8000)
        data = _loose_json(raw if isinstance(raw, str) else str(raw))
    notes = _dedup_notes(prev.get("notes"), data.get("notes"))
    prefs = _dedup_list(prev.get("prefs"), data.get("prefs"), 5)
    topics = _dedup_list(prev.get("topics"), data.get("topics"), 6)
    return {
        "notes": notes,
        "prefs": prefs,
        "topics": topics,
        "summary": str(data.get("summary") or "").strip()[:400],
        "raw_ok": bool(data),
        "rounds": len(msgs),
    }

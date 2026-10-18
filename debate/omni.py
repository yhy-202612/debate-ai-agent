"""百炼 Omni Realtime 端到端出稿通道（可选）。

与级联链路的分工：
- 听 + 判断谁在说话 → 仍由本项目的分片 ASR + 说话人归属负责（Omni 做不到说话人分离）
- 想 + 说 → 交给 Omni：文本输入（对方原话）→ 直接流式输出 PCM 音频 + 字幕

这样一步得到「语音」，省掉独立的 LLM 出稿 + TTS 两段，实测首字 0.25s、首音频包 0.6~0.8s。

事件格式（供 api/server.py 直接转 SSE）：
  {"type":"text","delta":str}        # 字幕增量（assistant 转写）
  {"type":"audio","delta":str}       # base64 的 PCM16 音频切片（24kHz 单声道）
  {"type":"done","text":str,...}     # 结束，附统计
  {"type":"error","message":str}
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import AsyncIterator

from . import knowledge, prompts
from .config import dashscope_key

RT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"
DEFAULT_VOICE = "Tina"
SAMPLE_RATE = 24000          # Omni 输出 PCM 采样率（实测约 24k）
AUDIO_FLUSH_BYTES = 6000     # 攒够约 0.125s 音频再发一次，减少 SSE 事件数
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 90


def model_id() -> str:
    import os
    return (os.environ.get("DEBATE_OMNI_MODEL") or DEFAULT_MODEL).strip()


def voice_id() -> str:
    import os
    return (os.environ.get("DEBATE_OMNI_VOICE") or DEFAULT_VOICE).strip()


def _header_kwarg() -> str:
    """websockets 12/13 用 extra_headers，14+ 改名 additional_headers。"""
    try:
        import websockets
        major = int(str(websockets.__version__).split(".")[0])
    except Exception:
        major = 14
    return "additional_headers" if major >= 14 else "extra_headers"


def build_instructions(*, our_side: str, our_case: str, duration: int = 45,
                       memory: str = "", extra: str = "",
                       no_strategy: bool = False,
                       continuation: dict | None = None) -> str:
    """把「人设 + 立场 + 时长约束 + 长期记忆」拼成 Omni 的 system。

    no_strategy=True 为训练版纯净模式：用 prompts.SYSTEM_CLEAN，
    不含辩论技法库与考察口径，只保留 TTS 对接所需的格式约束。
    """
    lo = max(120, int(duration * 4.5))
    hi = max(lo + 40, int(duration * 5.5))
    parts = [
        prompts.SYSTEM_CLEAN if no_strategy else prompts.SYSTEM,
        "",
        f"【我方立场】{our_side}",
        f"【我方立论】{our_case}",
        "",
        "【本次发言的硬约束】",
        f"- 这是本轮唯一的连续发言，时长约 {duration} 秒，请把整段话控制在 {lo}~{hi} 字之间，说完即止，不要展开成全面论述。",
        "- 只说口语，直接可播：不要「好的」「下面我来说」这类过场话。",
        "- 不要输出任何 Markdown 符号（星号、井号、项目符号），不要分行列表，就是一段自然的口语。",
        "- 必须以一段可直接朗读的话结束，不要在末尾补充说明或标注。",
    ]
    if continuation:
        parts += ["",
                  prompts.CONTINUE_NOTE.format(
                      part=int(continuation.get("part") or 2),
                      said=(str(continuation.get("said") or "").strip() or "（尚未开口）"),
                      fresh=(str(continuation.get("fresh") or "").strip() or "（对方没有新的补充）"),
                      remain=duration, lo=lo, hi=hi)]
    mem = (memory or "").strip()
    if mem:
        parts += ["", "【本场长期记忆（避免重复，保持连贯）】", mem]
    ext = (extra or "").strip()
    if ext:
        parts += ["", "【使用方自定义提示词（务必遵循；与上面默认风格冲突时以本段为准）】", ext]
    kb = knowledge.knowledge_block()
    if kb:
        parts += ["", kb]
    return "\n".join(parts)


async def rebut_stream_omni(*, opponent: str, our_side: str, our_case: str,
                            topic: str = "", duration: int = 45, memory: str = "",
                            voice: str = "", model: str = "",
                            system_extra: str = "", no_strategy: bool = False,
                            continuation: dict | None = None,
                            chunk_sec: int = 0) -> AsyncIterator[dict]:
    """文本入 → 文本 + PCM 音频流式出。异常统一转成 error 事件，不向上抛。"""
    key = dashscope_key()
    if not key:
        yield {"type": "error", "message": "未配置 DASHSCOPE_API_KEY，无法使用端到端通道"}
        return
    try:
        import websockets
    except Exception:
        yield {"type": "error", "message": "服务器缺少 websockets 依赖，请安装后重启"}
        return

    mdl = (model or model_id()).strip()
    voc = (voice or voice_id()).strip()
    # 分段 / 续写：本段只负责剩余时长那一段
    if continuation:
        duration = max(6, int(continuation.get("remain_sec") or duration))
    elif chunk_sec:
        duration = max(6, min(int(duration), int(chunk_sec)))
    instructions = build_instructions(our_side=our_side, our_case=our_case,
                                      duration=duration, memory=memory, extra=system_extra,
                                      no_strategy=no_strategy, continuation=continuation)
    # ★与队友去重：我方一辩/三辩已用过的例子，二辩不再重复
    try:
        from .engine import _no_repeat_note
        repeat_note = _no_repeat_note(opponent)
    except Exception:
        repeat_note = ""
    task = (f"【辩题】{topic}\n【对方刚刚的发言】\n{opponent}"
            f"{repeat_note}\n\n请按上面的要求，直接说出你的反驳。")
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
            "turn_detection": None,                            # 纯文本输入，不需要 VAD
        }}, ensure_ascii=False))
        while True:                                            # 等 session.updated
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

        async def flush(force: bool = False):
            nonlocal buf, last_emit, emitted
            if not buf:
                return
            # 首包立即发（不攒），之后攒够一块或超 0.2s 再发，减少 SSE 事件数
            if not force and emitted and len(buf) < AUDIO_FLUSH_BYTES and time.time() - last_emit < 0.2:
                return
            chunk = bytes(buf)
            buf = bytearray()
            last_emit = time.time()
            emitted = True
            yield {"type": "audio", "delta": base64.b64encode(chunk).decode()}

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                yield {"type": "error", "message": "端到端通道长时间无响应（已超时）"}
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
                    async for item in flush():
                        yield item
            elif et in ("response.done", "response.completed"):
                break
            elif et == "error":
                yield {"type": "error", "message": "端到端通道报错：" + json.dumps(ev, ensure_ascii=False)[:300]}
                break

        async for item in flush(force=True):
            yield item
        text = "".join(text_parts).strip()
        yield {"type": "done", "text": text, "voice": voc, "model": mdl,
               "sample_rate": SAMPLE_RATE,
               "first_text_ms": first_text_ms, "first_audio_ms": first_audio_ms,
               "ms": int((time.time() - t0) * 1000), "chars": len(text)}
    except Exception as exc:                                    # 连接/协议异常
        yield {"type": "error", "message": f"端到端通道不可用：{type(exc).__name__}: {exc}"[:300]}
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

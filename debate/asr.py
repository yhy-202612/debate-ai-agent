"""M2 语音层 · ASR：在线识别（硅基流动 SenseVoice/Qwen3-ASR，或阿里云百炼 Qwen-Audio-ASR）。

服务端调用，密钥不出服务器。前端可经 ?model=<id> 切换（见 ASR_MODELS）。
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import re

import httpx

from .config import (ASR_HOTWORDS, ASR_MODELS, asr_dual_aux,
                     asr_dual_aux_timeout, asr_dual_primary, asr_model,
                     asr_provider, dashscope_base_url, dashscope_key,
                     siliconflow_base, siliconflow_key)
from .models import ChatClient, ModelError


class AsrError(RuntimeError):
    """语音识别失败。"""


async def transcribe(audio: bytes, filename: str = "seg.wav",
                     content_type: str = "audio/wav", model: str = "",
                     prompt: str = "") -> str:
    """把一段音频字节交给在线 ASR 识别，返回文本；model 为空/非法时用默认模型。

    prompt 为「上文提示」（辩题 + 上一段识别原文），可显著改善术语/人名/语境一致性。
    服务商按模型 id 自动路由：siliconflow（OpenAI 兼容）或 bailian（DashScope 同步接口）。
    """
    if not audio:
        raise AsrError("音频为空")
    allowed = {m["id"] for m in ASR_MODELS}
    chosen = model if model in allowed else asr_model()
    provider = asr_provider(chosen)
    if provider == "dual":
        return await _transcribe_dual(audio, filename, content_type, prompt)
    if provider == "bailian":
        return await _transcribe_bailian(audio, chosen, content_type, prompt)
    return await _transcribe_siliconflow(audio, filename, content_type, chosen, prompt)


# ---------------- 并行融合（正文用 primary，热词校正借 aux）----------------

def _merge_hotwords(primary: str, aux: str) -> tuple[str, list[str]]:
    """用「带热词的 aux 结果」校正 primary 中的辩论术语。

    仅在两个条件同时成立时才替换：①aux 确实识别出了该热词（有依据）；
    ②primary 中存在与它等长且高度相似的片段（确实是被听错的同一处）。
    长词优先处理，避免短词先替换破坏长词。
    """
    if not primary or not aux:
        return primary, []
    out = primary
    changed: list[str] = []
    for w in sorted(ASR_HOTWORDS, key=len, reverse=True):
        if len(w) < 2 or w in out or w not in aux:
            continue
        n = len(w)
        best_i, best_r = None, 0.0
        for i in range(len(out) - n + 1):
            seg = out[i:i + n]
            if seg == w:
                best_i, best_r = None, 1.0       # 已存在（理论上不会走到）
                break
            r = difflib.SequenceMatcher(None, seg, w).ratio()
            if r > best_r:
                best_i, best_r = i, r
        # 等长替换 + 至少一半字符相同（2 字词 0.5，长词 0.6）
        thresh = 0.5 if n <= 2 else 0.6
        if best_i is not None and best_r >= thresh:
            out = out[:best_i] + w + out[best_i + n:]
            changed.append(w)
    return out, changed


async def _transcribe_dual(audio: bytes, filename: str,
                           content_type: str, prompt: str) -> str:
    """两路并行识别：正文取 primary（默认硅基流动 Qwen3-ASR），
    再用 aux（默认百炼 Qwen-Audio-3.1-ASR，带热词）校正其中的辩论术语。
    并行不叠加耗时；任一路失败时自动降级为另一路，提高可用性。
    """
    pid, aid = asr_dual_primary(), asr_dual_aux()

    async def _run(mid: str):
        try:
            if asr_provider(mid) == "bailian":
                return await _transcribe_bailian(audio, mid, content_type, prompt), None
            return await _transcribe_siliconflow(audio, filename, content_type, mid, prompt), None
        except AsrError as exc:
            return "", exc

    ptask = asyncio.create_task(_run(pid))
    atask = asyncio.create_task(_run(aid))
    pt, perr = await ptask                      # 主路决定正文，必须等
    try:
        # ★ 辅路只负责「热词校正」，不能拖慢主文本：给一个时间预算，
        #   超时即放弃校正直接用主路结果（避免上游偶发慢请求把整段识别拖到十几秒）。
        at, aerr = await asyncio.wait_for(atask, timeout=asr_dual_aux_timeout())
    except asyncio.TimeoutError:
        atask.cancel()
        at, aerr = "", "辅路超时/失败"
    if pt:                                      # 主路成功：以其为正文，叠加 aux 热词校正
        merged, _changed = _merge_hotwords(pt, at)
        return merged
    if at:                                      # 主路失败 → aux 兜底
        return at
    raise AsrError(f"两路识别均失败：主={perr}；辅={aerr}")


async def _transcribe_siliconflow(audio: bytes, filename: str,
                                  content_type: str, model: str, prompt: str) -> str:
    """硅基流动：OpenAI 兼容 /audio/transcriptions（multipart 上传）。"""
    key = siliconflow_key()
    if not key:
        raise AsrError("未配置 SILICONFLOW_API_KEY")
    url = siliconflow_base().rstrip("/") + "/audio/transcriptions"
    files = {"file": (filename, audio, content_type)}
    data = {"model": model}
    ctx = (prompt or "").strip()
    if ctx:
        data["prompt"] = ctx[:240]
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers={"Authorization": "Bearer " + key},
                                     files=files, data=data)
    except httpx.HTTPError as exc:
        raise AsrError(f"识别请求失败：{type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise AsrError(f"识别失败：HTTP {resp.status_code} {resp.text[:160]}")
    try:
        obj = resp.json()
    except ValueError as exc:
        raise AsrError("识别返回不是合法 JSON") from exc
    return str(obj.get("text") or "").strip()


# ---------------- 阿里云百炼（Qwen-Audio-ASR 同步接口）----------------

_MIME_EXT = {
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/opus": "ogg",
    "audio/mp4": "mp4", "audio/m4a": "m4a", "audio/aac": "aac",
}
_BAILIAN_FMT_OK = {"wav", "mp3", "webm", "ogg", "mp4", "m4a", "aac", "pcm"}


async def _transcribe_bailian(audio: bytes, model: str,
                              content_type: str, prompt: str) -> str:
    """百炼同步接口：Base64 Data URI 直传（无需公网 URL），支持即时热词与上文提示。

    端点 /api/v1/services/aigc/multimodal-generation/generation（≤5 分钟音频同步返回）。
    返回结构特殊：文本在 output.text，兜底 output.output.sentence.text。
    """
    key = dashscope_key()
    if not key:
        raise AsrError("未配置 DASHSCOPE_API_KEY（百炼）")
    mime = (content_type or "").split(";")[0].strip().lower() or "audio/wav"
    fmt = _MIME_EXT.get(mime, "wav")
    if fmt not in _BAILIAN_FMT_OK:
        raise AsrError(f"百炼不支持该音频格式：{mime}")
    b64 = base64.b64encode(audio).decode()
    content: list[dict] = []
    ctx = (prompt or "").strip()
    if ctx:
        content.append({"type": "input_text", "text": ctx[:400]})   # 上文提示（改善语境一致性）
    content.append({"type": "input_audio",
                    "input_audio": {"data": f"data:audio/{fmt};base64,{b64}"}})
    body = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"format": fmt, "vocabulary": ASR_HOTWORDS},
    }
    url = dashscope_base_url().rstrip("/") + "/api/v1/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
               "X-DashScope-SSE": "disable"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise AsrError(f"识别请求失败：{type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise AsrError(f"识别失败：HTTP {resp.status_code} {resp.text[:160]}")
    try:
        obj = resp.json()
    except ValueError as exc:
        raise AsrError("识别返回不是合法 JSON") from exc
    out = obj.get("output") or {}
    text = (out.get("text")
            or ((out.get("output") or {}).get("sentence") or {}).get("text")
            or "")
    if not str(text).strip():
        msg = obj.get("message") or out.get("message") or obj.get("code") or ""
        raise AsrError("识别返回无文本" + (f"：{str(msg)[:120]}" if msg else ""))
    return str(text).strip()


# ---------------- 说话人分离解析 ----------------

_DIAR_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(.*)$")


def parse_diarize(text: str) -> list[dict]:
    """解析说话人分离模型输出（形如「1: 文本」逐行），返回 [{speaker, text}]。

    非编号行并入上一段；无编号行则返回空列表（调用方回退普通归属）。
    """
    src = (text or "").strip()
    if not src:
        return []
    segs: list[dict] = []
    for raw in src.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _DIAR_RE.match(line)
        if m:
            segs.append({"speaker": m.group(1), "text": m.group(2).strip()})
        elif segs:
            segs[-1]["text"] = (segs[-1]["text"] + " " + line).strip()
    return [s for s in segs if s["text"]]


# ---------------- 识别文本自动修正（可选） ----------------

FIX_SYSTEM = ("你是语音识别纠错助手。只修正明显的识别错误，绝不改变原意、不增删信息。")

# 辩论场景常见易错词（提示模型优先在此类词上纠正同音错误）
DEBATE_TERMS = (
    "弊大于利、利大于弊、立论、驳论、归谬、滑坡、稻草人、以偏概全、因果倒置、"
    "举证责任、隐含前提、衡量标准、可行性、边际、结构性失业、再培训、"
    "供给侧、需求侧、溢出效应、同质化、版权、伦理、监管、垄断、算法、"
    "可持续、议题、质询、陈词、共识、反驳、论证"
)

FIX_PROMPT = (
    "下面是一段辩论现场的语音识别文字，可能存在同音错别字、缺失标点、重复词或断句错误。"
    "请在不改变原意、不增加或删除信息的前提下，修正为通顺的书面文字；"
    "保留专有名词与口语风格。特别注意下列辩论常用词的误识别"
    "（例如把“弊”听成“必”、“利”听成“力”、“驳论”听成“拨论”）："
    + DEBATE_TERMS +
    "。只输出修正后的文字，不要任何解释或引号。\n原文：{text}"
)


async def polish(text: str, client: ChatClient) -> str:
    """用快模型轻量纠正识别错误；结果异常（空/长度离谱）或超时则回退原文。"""
    src = (text or "").strip()
    if not src or client is None:
        return src
    try:
        # deepseek-flash 等推理模型 reasoning 消耗大：给足 max_tokens 防 content 截断为空，
        # 同时加超时兜底（偶发长推理不拖住整条识别队列）。
        fixed = await asyncio.wait_for(
            client.complete(FIX_SYSTEM, FIX_PROMPT.replace("{text}", src),
                            temperature=0.0, max_tokens=8000),
            timeout=20.0)
    except (ModelError, asyncio.TimeoutError):
        return src
    fixed = (fixed or "").strip().strip("“”\"'")
    if not fixed or len(fixed) > len(src) * 3 or len(fixed) < len(src) * 0.3:
        return src
    return fixed

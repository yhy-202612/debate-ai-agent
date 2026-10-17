"""M2 语音层：优先硅基流动 CosyVoice 2 在线合成，失败自动回退 edge-tts。

密钥仅从环境变量读取（服务端调用），不下发前端。
"""
from __future__ import annotations

import asyncio
import contextlib
import re

import httpx

from .config import tts_model, tts_rate, tts_speaker
from .config import siliconflow_base, siliconflow_key
from .config import dashscope_key, dashscope_base_url

# ---------------- 模型与音色注册表 ----------------
# provider: siliconflow（硅基流动，OpenAI 风格 /audio/speech，WAV/MP3 流式）
#         | aliyun（阿里云百炼，DashScope SDK WebSocket，WAV 流式）
#         | aliyun_http（阿里云百炼，HTTP 非实时，整段返回 WAV 的 URL）
# 音色名均经线上实测可用（2026-10-14/15），避免用户选到报错音色。

V3FLASH_VOICES = [
    ("longanyang", "龙安洋 · 男声（沉稳）"), ("longanhuan", "龙安欢 · 女声"),
    ("longanshuo_v3", "龙安朔 · 男声"), ("longfeifei_v3", "龙菲菲 · 女声"),
    ("longyichen_v3", "龙逸尘 · 男声"), ("longanzhi_v3", "龙安智 · 男声"),
    ("longanqin_v3", "龙安亲 · 女声"), ("longanwen_v3", "龙安温 · 女声"),
    ("longanling_v3", "龙安灵 · 女声"), ("longanya_v3", "龙安雅 · 女声"),
    ("longwangwang_v3", "龙汪汪 · 少年音"),
]
V2_VOICES = [
    ("longxiaochun_v2", "龙小淳 · 女声"), ("longxiaoxia_v2", "龙小夏 · 女声"),
    ("longyumi_v2", "YUMI · 女声"), ("longanli", "龙安莉 · 女声"),
    ("longanlang", "龙安朗 · 男声"), ("longanyun", "龙安昀 · 男声"),
    ("longling_v2", "龙铃 · 女声"), ("longjielidou_v2", "龙杰力豆 · 童声"),
]
QWEN3_TTS_VOICES = [
    ("Cherry", "Cherry · 女声"), ("Serena", "Serena · 女声"),
    ("Ethan", "Ethan · 男声"), ("Chelsie", "Chelsie · 女声"),
    ("Dylan", "Dylan · 男声"), ("Jada", "Jada · 女声"),
    ("Sunny", "Sunny · 女声"), ("Peter", "Peter · 男声"),
]

# Qwen-Audio-3.0-TTS-Flash 系统音色（实测可用，2026-10-15）
QA30_FLASH_VOICES = [
    ("longanhuan_v3.6", "龙安欢 · 女声（默认·自然亲切）"),
    ("longanfengyue", "龙安风悦 · 女声（自然亲切）"),
    ("longanyuanfei", "龙安元妃 · 女声（高傲）"),
    ("longanlingxi", "龙安灵希 · 女声（可爱甜美）"),
    ("longanxiaoxin", "龙安小昕 · 女声（亲切活泼）"),
    ("longjielidou_v3.6", "龙杰力豆 · 男童（天真）"),
    ("longhuohuo_v3.6", "龙火火 · 少年（顽皮）"),
    ("longchuanshu_v3.6", "龙川叔 · 男声（川普大叔）"),
    ("loongjohn", "loongjohn · 男声（沉稳亲切美音）"),
]
# Qwen-Audio-3.0-TTS-Plus 系统音色（旗舰，仅 2 个）
QA30_PLUS_VOICES = [
    ("longanlingxin", "龙安灵心 · 女声（知心温暖·旗舰）"),
    ("longanlufeng", "龙安鲁风 · 男声（明亮开朗）"),
]

# Qwen-Audio-3.1-TTS-Flash 系统音色（⚠️ 音色带 _v3.1 后缀，与 3.0 不通用！
# 用 3.0 音色会报 `[cosyvoice:]Engine error [411]`）
QA31_FLASH_VOICES = [
    ("longanhuan_v3.1", "龙安欢 · 女声（默认·多语种方言）"),
    ("longanlingxin_v3.1", "龙安灵心 · 女声（知心温暖）"),
    ("longanfengyue_v3.1", "龙安风悦 · 女声（自然亲切）"),
    ("xunanchuan_v3.1", "许南川 · 男声（多语种方言）"),
    ("anmingyuan_v3.1", "安明远 · 男声（清亮自然）"),
    ("huozhuoshi_v3.1", "霍拙石 · 男声（清亮）"),
    ("longanzhi_v3.1", "龙安智 · 男声（睿智轻熟）"),
    ("xuyanchu_v3.1", "许言初 · 女声（沉稳磁性）"),
    ("yunhuanhuan_v3.1", "云欢欢 · 女声（高亢热情）"),
    ("wenhuaiqing_v3.1", "温怀清 · 女声（清亮柔和）"),
    ("yeqinghe_v3.1", "叶清禾 · 女声（亲切温柔）"),
    ("baianran_v3.1", "白安然 · 女声（低沉浑厚）"),
]

TTS_MODELS: list[dict] = [
    {"id": "FunAudioLLM/CosyVoice2-0.5B", "provider": "siliconflow", "streaming": True,
     "name": "CosyVoice 2 · 硅基流动（推荐）",
     "note": "中文自然度最好；流式首字节约 0.1~0.3 秒；实测字错率 0%",
     "voices": [("alex", "alex · 男声（沉稳有力）"), ("benjamin", "benjamin · 男声（沉稳）"),
                ("charles", "charles · 男声"), ("david", "david · 男声"),
                ("anna", "anna · 女声"), ("bella", "bella · 女声"),
                ("claire", "claire · 女声"), ("diana", "diana · 女声")]},
    {"id": "fnlp/MOSS-TTSD-v0.5", "provider": "siliconflow", "streaming": True,
     "name": "MOSS-TTSD · 硅基流动（双人对话）",
     "note": "对话合成模型，适合双人播客；单人播报清晰度略逊（实测字错率 3.7%）",
     "voices": [("alex", "alex · 男声（沉稳有力）"), ("anna", "anna · 女声"),
                ("bella", "bella · 女声"), ("david", "david · 男声")]},
    {"id": "cosyvoice-v3-flash", "provider": "aliyun", "streaming": True,
     "name": "百炼 CosyVoice-v3-Flash · 阿里云",
     "note": "商业版新模型；支持指令控制情感/语气；实测字错率 0%；首字节约 0.6 秒",
     "voices": V3FLASH_VOICES},
    {"id": "cosyvoice-v2", "provider": "aliyun", "streaming": True,
     "name": "百炼 CosyVoice-v2 · 阿里云",
     "note": "70+ 音色、支持方言音色；实测字错率 0%",
     "voices": V2_VOICES},
    {"id": "qwen3-tts-flash", "provider": "aliyun_http", "streaming": False,
     "name": "百炼 Qwen3-TTS-Flash · 阿里云（整段）",
     "note": "旗舰音质；但为非实时接口（整段生成，首字延迟较高）",
     "voices": QWEN3_TTS_VOICES},
    {"id": "qwen-audio-3.0-tts-flash", "provider": "aliyun_audio", "streaming": True,
     "name": "百炼 Qwen-Audio-3.0-TTS-Flash · 阿里云",
     "note": "新旗舰；支持情感标签（愤怒/激动/耳语…）与自然语言指令；真流式",
     "voices": QA30_FLASH_VOICES},
    {"id": "qwen-audio-3.0-tts-plus", "provider": "aliyun_audio", "streaming": True,
     "name": "百炼 Qwen-Audio-3.0-TTS-Plus · 阿里云（音质最佳）",
     "note": "语音榜第一；仅 2 个系统音色；支持情感标签与指令；整段/流式",
     "voices": QA30_PLUS_VOICES},
    {"id": "qwen-audio-3.1-tts-flash", "provider": "aliyun_audio", "streaming": True,
     "name": "百炼 Qwen-Audio-3.1-TTS-Flash · 阿里云（音色最多）",
     "note": "最新版；音色最丰富（含男声/方言）；支持情感标签、指令控制、SSML",
     "voices": QA31_FLASH_VOICES},
]

_MODEL_IDS = {m["id"] for m in TTS_MODELS}


def resolve_model(model: str | None) -> str:
    """把前端传来的模型名收敛到白名单，非法或为空则回落默认模型。"""
    if model and model in _MODEL_IDS:
        return model
    return tts_model()


def model_spec(model: str) -> dict:
    m = resolve_model(model)
    for it in TTS_MODELS:
        if it["id"] == m:
            return it
    return TTS_MODELS[0]


def voices_for(model: str) -> list[dict]:
    """返回某模型可用的音色清单（含中文名）。"""
    return [{"id": v[0], "name": v[1]} for v in model_spec(model).get("voices", [])]


def default_voice_for(model: str) -> str:
    vs = model_spec(model).get("voices", [])
    return vs[0][0] if vs else DEFAULT_VOICE


# CosyVoice 2 内置音色（硅基流动）
VOICES: list[dict] = [
    {"id": "alex", "name": "alex · 男声（沉稳有力）", "gender": "男"},
    {"id": "benjamin", "name": "benjamin · 男声（沉稳）", "gender": "男"},
    {"id": "charles", "name": "charles · 男声", "gender": "男"},
    {"id": "david", "name": "david · 男声", "gender": "男"},
    {"id": "anna", "name": "anna · 女声", "gender": "女"},
    {"id": "bella", "name": "bella · 女声", "gender": "女"},
    {"id": "claire", "name": "claire · 女声", "gender": "女"},
    {"id": "diana", "name": "diana · 女声", "gender": "女"},
]
SPEAKERS = {v["id"] for v in VOICES}
DEFAULT_VOICE = "alex"

# edge-tts 兜底音色（硅基流动不可用时）
_EDGE_FALLBACK_VOICE = "zh-CN-YunjianNeural"


def rate_to_speed(rate: str) -> float:
    """把 '+8%' 之类的语速转成 CosyVoice 的 speed 倍率。

    上游 CosyVoice2 实测 speed 1.0~4.0 均可用（超范围 clamp）；『倍率』= 1 + 百分比。
    """
    m = re.search(r"[+-]?\d+", rate or "")
    pct = int(m.group(0)) if m else 8
    return max(0.5, min(3.0, 1 + pct / 100.0))


async def _synth_siliconflow(text: str, speaker: str, speed: float, model: str | None = None) -> bytes:
    key = siliconflow_key()
    if not key:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY")
    model = model or tts_model()
    payload = {
        "model": model,
        "input": text,
        "voice": f"{model}:{speaker}",
        "response_format": "mp3",
        "speed": speed,
    }
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            siliconflow_base().rstrip("/") + "/audio/speech",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:160]}")
    if not resp.content:
        raise RuntimeError("语音合成为空")
    return resp.content


async def _synth_edge(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError("语音合成为空")
    return bytes(buf)


# ---------------- 阿里云百炼（DashScope）----------------

def _aliyun_speech_rate(rate: str) -> float:
    """百炼 speech_rate 取值范围 0.5~2.0（1.0 为原速）。"""
    return max(0.5, min(2.0, rate_to_speed(rate)))


# 情感/语气标签（文本内联，如「[严肃]各位评委…」）。
# ⚠️ 各模型标签集不同：CosyVoice 用**中文**标签、Qwen-Audio-TTS 用**英文**标签；
#    传入不支持的标签会**导致整段合成失败**，故按模型严格白名单，其它一律忽略。
# 展示名统一用中文，实际标签按模型映射（见 EMOTION_SETS）。
_CN_EMOTIONS = ["严肃", "激动", "自信", "平静", "悲伤", "愤怒", "开心", "笑"]
_EN_EMOTIONS = {"严肃": "serious", "激动": "excited", "愤怒": "angry", "悲伤": "sad",
                "大喊": "shouting", "耳语": "whispers", "轻蔑": "scornful", "好奇": "curious",
                "大笑": "laughing", "叹息": "sighing", "疲惫": "tired", "恐慌": "panicked"}
EMOTION_SETS: dict[str, dict[str, str]] = {
    "cosyvoice-v3-flash": {k: k for k in _CN_EMOTIONS},
    "qwen-audio-3.0-tts-flash": dict(_EN_EMOTIONS),
    "qwen-audio-3.0-tts-plus": dict(_EN_EMOTIONS),
    "qwen-audio-3.1-tts-flash": dict(_EN_EMOTIONS),
}
# 兼容旧引用（默认模型 CosyVoice 的标签）
EMOTION_TAGS: list[str] = _CN_EMOTIONS
_EMOTION_MODELS = set(EMOTION_SETS)


def supports_emotion(model: str | None) -> bool:
    return model_spec(model)["id"] in _EMOTION_MODELS


def emotions_for(model: str | None) -> list[str]:
    """返回该模型可用的情感标签展示名（不支持则空列表）。"""
    return list(EMOTION_SETS.get(model_spec(model)["id"], {}).keys())


def _apply_emotion(text: str, emotion: str | None, model: str) -> str:
    e = (emotion or "").strip()
    mapping = EMOTION_SETS.get(model, {})
    if not e or e not in mapping:
        return text
    return f"[{mapping[e]}]" + text


async def _stream_wav_aliyun(text: str, voice: str, model: str, rate: str,
                             emotion: str | None = None):
    """百炼流式合成（DashScope SDK / WebSocket）。

    SDK 为「同步阻塞 + 回调」式，这里放到后台线程执行，把音频块桥接到 asyncio 队列，
    仍是「边合成边下发」，前端可边收边播。输出为 24kHz/16bit/单声道 WAV。
    """
    key = dashscope_key()
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")
    text = _apply_emotion(text, emotion, model)      # 情感/语气标签（白名单）

    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[tuple[str, object]]" = asyncio.Queue()

    def worker() -> None:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback, AudioFormat

        dashscope.api_key = key

        class _CB(ResultCallback):
            def on_data(self, data) -> None:
                loop.call_soon_threadsafe(q.put_nowait, ("d", bytes(data)))

            def on_error(self, msg) -> None:
                loop.call_soon_threadsafe(q.put_nowait, ("e", str(msg)))

            def on_complete(self) -> None:
                pass

            def on_close(self) -> None:
                pass

        try:
            syn = SpeechSynthesizer(
                model=model, voice=voice,
                format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                speech_rate=_aliyun_speech_rate(rate), callback=_CB(),
            )
            syn.streaming_call(text)
            syn.streaming_complete()
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, ("e", f"{type(exc).__name__}: {exc}"[:200]))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, ("done", None))

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            kind, payload = await q.get()
            if kind == "d":
                yield payload  # type: ignore[misc]
            elif kind == "e":
                raise RuntimeError(str(payload))
            else:
                break
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=10)


async def _stream_wav_aliyun_http(text: str, voice: str, model: str, rate: str):
    """百炼非实时（HTTP）合成：整段生成后返回音频 URL，再整段转发（首字延迟较高）。"""
    key = dashscope_key()
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")
    url = dashscope_base_url().rstrip("/") + "/api/v1/services/aigc/multimodal-generation/generation"
    payload = {"model": model, "input": {"text": text, "voice": voice},
               # ★ 本端点（multimodal-generation）语速字段是 parameters.speech_rate；
               #   实测 rate / speed 两个字段在本接口下无效或反向。
               "parameters": {"speech_rate": _aliyun_speech_rate(rate)}}
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            url,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:160]}")
        data = resp.json()
        audio = ((data.get("output") or {}).get("audio") or {})
        audio_url = audio.get("url")
        if not audio_url:
            raise RuntimeError(f"百炼未返回音频：{str(data)[:160]}")
        async with client.stream("GET", audio_url) as r2:
            if r2.status_code >= 400:
                raise RuntimeError(f"音频下载失败 HTTP {r2.status_code}")
            async for chunk in r2.aiter_bytes():
                if chunk:
                    yield chunk


async def _stream_wav_aliyun_audio(text: str, voice: str, model: str, rate: str,
                                   emotion: str | None = None):
    """百炼 Qwen-Audio-TTS 合成（HTTP，端点 SpeechSynthesizer）。

    - 非流式：整段返回音频 URL；
    - 流式：加请求头 ``X-DashScope-SSE: enable``，服务端以 SSE 逐段返回 base64 音频。
    这里统一走**流式**，实现边合成边下发（首字延迟低于整段模式）。
    输出为 24kHz/16bit/单声道 WAV。
    """
    import base64
    import json as _json

    key = dashscope_key()
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")
    text = _apply_emotion(text, emotion, model)      # 情感标签（按模型白名单，英文标签）
    url = dashscope_base_url().rstrip("/") + "/api/v1/services/audio/tts/SpeechSynthesizer"
    payload = {"model": model,
               "input": {"text": text, "voice": voice, "format": "wav", "sample_rate": 24000},
               # ★ 语速必须放在 parameters.rate（浮点倍率 0.5~2.0）——实测 speed / speech_rate
               #   两个字段在本接口下【完全无效】，只有 rate 会真正改变音频时长。
               "parameters": {"rate": _aliyun_speech_rate(rate)}}
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
               "X-DashScope-SSE": "enable"}
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread())[:200]
                raise RuntimeError(f"HTTP {resp.status_code} {body.decode('utf-8', 'ignore')}")
            async for line in resp.aiter_lines():
                line = (line or "").strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    data = _json.loads(raw)
                except Exception:  # noqa: BLE001 跳过非 JSON 行
                    continue
                audio = ((data.get("output") or {}).get("audio") or {})
                b64 = audio.get("data")
                if b64:
                    yield base64.b64decode(b64)


def _pick_voice(spec: dict, voice: str | None) -> str:
    """在模型允许的音色里挑选：合法就用它，否则回落该模型默认音色。"""
    vs = spec.get("voices", [])
    if voice and any(voice == v[0] for v in vs):
        return voice
    return vs[0][0] if vs else DEFAULT_VOICE


async def synthesize(text: str, voice: str = DEFAULT_VOICE, rate: str = "+8%",
                     model: str | None = None, emotion: str | None = None) -> bytes:
    """合成一段文本（整段）。百炼模型经流式接口收集；硅基流动失败回退 edge-tts。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("文本为空")
    spec = model_spec(model)
    mid, provider = spec["id"], spec["provider"]
    if provider in ("aliyun", "aliyun_http", "aliyun_audio"):
        buf = bytearray()
        async for chunk in stream_wav(text, voice, rate, mid, emotion):
            buf.extend(chunk)
        if not buf:
            raise RuntimeError("语音合成为空")
        return bytes(buf)
    if siliconflow_key():
        speaker = voice if voice in SPEAKERS else tts_speaker()
        try:
            return await _synth_siliconflow(text, speaker, rate_to_speed(rate), mid)
        except Exception:  # noqa: BLE001 回退到 edge-tts
            pass
    return await _synth_edge(text, _EDGE_FALLBACK_VOICE, rate)


async def stream_wav(text: str, voice: str = DEFAULT_VOICE, rate: str = "+8%",
                     model: str | None = None, emotion: str | None = None):
    """流式合成（WAV，24kHz/16bit/单声道），前端按 RIFF 解析 PCM 边收边播。

    - 硅基流动 /audio/speech：上游本身 chunked 返回 WAV，边收边转发；
    - 阿里云百炼 aliyun：DashScope SDK WebSocket 流式；
    - 阿里云百炼 aliyun_http：非实时，整段生成后转发（首字延迟较高）。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("文本为空")
    spec = model_spec(model)
    mid, provider = spec["id"], spec["provider"]
    if provider == "aliyun":
        async for chunk in _stream_wav_aliyun(text, _pick_voice(spec, voice), mid, rate, emotion):
            yield chunk
        return
    if provider == "aliyun_http":
        async for chunk in _stream_wav_aliyun_http(text, _pick_voice(spec, voice), mid, rate):
            yield chunk
        return
    if provider == "aliyun_audio":
        async for chunk in _stream_wav_aliyun_audio(text, _pick_voice(spec, voice), mid, rate, emotion):
            yield chunk
        return
    # 硅基流动
    key = siliconflow_key()
    if not key:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY")
    speaker = voice if voice in SPEAKERS else tts_speaker()
    payload = {
        "model": mid,
        "input": text,
        "voice": f"{mid}:{speaker}",
        "response_format": "wav",
        "speed": rate_to_speed(rate),
    }
    url = siliconflow_base().rstrip("/") + "/audio/speech"
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", url,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread())[:200]
                raise RuntimeError(f"HTTP {resp.status_code} {body.decode('utf-8', 'ignore')}")
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk


def split_sentences(text: str, limit: int = 60) -> list[str]:
    """按标点与长度切句，配合前端逐句播报。"""
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？；!?;\n" or len(buf) >= limit:
            chunks.append(buf.strip())
            buf = ""
    if buf.strip():
        chunks.append(buf.strip())
    out: list[str] = []
    for seg in chunks:
        while len(seg) > limit * 2:
            cut = max((seg.rfind(c, 0, limit) for c in "，,、"), default=-1)
            if cut <= 0:
                cut = limit
            out.append(seg[:cut + 1].strip())
            seg = seg[cut + 1:]
        if seg.strip():
            out.append(seg.strip())
    return [s for s in out if s]

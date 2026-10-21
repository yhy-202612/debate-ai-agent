"""辩论 AI 二辩 · M1–M3 HTTP 服务。

挂载在班级网站子路径 /debate/ 下（由 nginx 去掉前缀转发），
与班级网站无任何数据与鉴权耦合。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from debate import asr as asr_mod
from debate import chat as chat_mod
from debate import chatstore
from debate import materials as materials_mod
from debate import conductor
from debate import factcheck as factcheck_mod
from debate import metrics as metrics_mod
from debate import omni as omni_mod
from debate import store
from debate import tts as tts_mod
from debate.config import (ASR_MODELS, access_token, asr_model, dashscope_key,
                           tts_rate, tts_voice)
from debate.engine import Engine
from debate.models import ModelError

engine = Engine()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _startup() -> None:
    info = engine.describe()
    print("[debate] routes:", json.dumps(info, ensure_ascii=False), flush=True)
    if not engine.routes["strong"].ready:
        print("[debate] 警告：strong 路由未就绪（缺少 API Key）", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _startup()
    yield


app = FastAPI(title="辩论 AI 二辩", version="1.1.0", lifespan=lifespan)

# 本地版以 file:// 或 http://localhost 打开，需跨域访问本服务
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth(request: Request) -> None:
    token = access_token()
    if not token:
        return
    got = request.headers.get("x-debate-token") or request.query_params.get("token")
    if got != token:
        raise HTTPException(status_code=401, detail="访问口令不正确")


def _validate(payload: dict) -> dict:
    opponent = str(payload.get("opponent") or "").strip()
    our_side = str(payload.get("our_side") or "").strip()
    our_case = str(payload.get("our_case") or "").strip()
    topic = str(payload.get("topic") or "人工智能的发展对人的作用与意义是积极还是消极").strip()
    try:
        duration = int(payload.get("duration") or 15)
    except (TypeError, ValueError):
        duration = 15
    if not opponent or not our_side or not our_case:
        raise HTTPException(status_code=400, detail="对方立论、我方立场、我方立论均不能为空")
    if not 10 <= duration <= 30:
        raise HTTPException(status_code=400, detail="目标时长须在 10-30 秒之间（赛制要求单轮 10-20 秒）")
    if len(opponent) > 12000 or len(our_case) > 6000:
        raise HTTPException(status_code=400, detail="输入过长，请先裁剪")
    # ★ 本场我方立场（拓展/消解）：与队别解耦，传入即优先，不传由队别兜底
    stance = str(payload.get("stance") or "").strip()[:40]
    # 使用方自定义提示词（反方训练版等用它调教风格；限长防滥用）
    extra = str(payload.get("system_extra") or "").strip()[:3000]
    # 纯净模式（训练版默认）：不注入辩论技法与考察口径，只保留 TTS 对接所需的格式约束
    no_strategy = bool(payload.get("no_strategy"))
    # ── 分段 / 续写（边播边生成）──
    chunk_sec = int(payload.get("chunk_sec") or 0)
    chunk_sec = max(0, min(int(duration) or 15, chunk_sec))
    raw_cont = payload.get("continuation")
    continuation = None
    if isinstance(raw_cont, dict):
        continuation = {
            "part": max(2, min(9, int(raw_cont.get("part") or 2))),
            "said": str(raw_cont.get("said") or "").strip()[:1500],
            "fresh": str(raw_cont.get("fresh") or "").strip()[:1500],
            "remain_sec": max(6, min(int(duration) or 15, int(raw_cont.get("remain_sec") or 20))),
        }
    # ★ 队内资料：默认开启（正方训练版 / 对话模式）；反方训练版显式传 false
    with_materials = payload.get("materials") is not False
    return {"opponent": opponent, "our_side": our_side, "our_case": our_case,
            "topic": topic, "duration": duration, "system_extra": extra,
            "stance": stance,
            "no_strategy": no_strategy, "chunk_sec": chunk_sec,
            "continuation": continuation, "materials": with_materials}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "engine": engine.describe(), "metrics": metrics_mod.summary()}


@app.get("/api/voices")
async def voices(model: str | None = None):
    """音色与合成模型清单；带 ?model= 时额外返回该模型可用的音色与默认音色。"""
    m = tts_mod.resolve_model(model)
    models = [{**it, "supports_emotion": tts_mod.supports_emotion(it["id"]),
               "emotions": tts_mod.emotions_for(it["id"])}
              for it in tts_mod.TTS_MODELS]
    return {"voices": tts_mod.VOICES, "default": tts_voice(), "rate": tts_rate(),
            "models": models, "default_model": tts_mod.model_spec(None)["id"],
            "model_voices": tts_mod.voices_for(m), "model_default_voice": tts_mod.default_voice_for(m),
            "emotions": tts_mod.emotions_for(m)}


@app.get("/api/asr-models")
async def asr_models():
    """可供前端选择的识别模型清单。"""
    return {"models": ASR_MODELS, "default": asr_model()}


@app.get("/api/models")
async def models():
    """可切换的出稿模型清单（仅名称与可用性，不含任何密钥信息）。"""
    return {"models": engine.models_info()}


@app.post("/api/rebut")
async def rebut(request: Request):
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    params = _validate(body)
    model = str(body.get("model") or "").strip()

    async def gen():
        yield ": connected\n\n"
        async for event in engine.rebut_stream(**params, model=model):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------- 端到端出稿通道（百炼 Omni Realtime，可选）----------------
OMNI_MODELS = [
    {"id": "qwen3.5-omni-plus-realtime", "label": "Omni Plus（质量优先，实测 3.3s）"},
    {"id": "qwen3.5-omni-flash-realtime", "label": "Omni Flash（速度优先，实测 2.4s）"},
]
OMNI_VOICES = [
    {"id": "Tina", "label": "Tina（女）"},
    {"id": "Serena", "label": "Serena（女）"},
    {"id": "Kiki", "label": "Kiki（女）"},
    {"id": "Sunny", "label": "Sunny（女）"},
    {"id": "Ethan", "label": "Ethan（男）"},
    {"id": "Dylan", "label": "Dylan（男）"},
    {"id": "Peter", "label": "Peter（男）"},
    {"id": "Rocky", "label": "Rocky（男）"},
    {"id": "Li", "label": "Li（男）"},
    {"id": "Marcus", "label": "Marcus（男）"},
]


@app.get("/api/omni-status")
async def omni_status():
    """端到端通道可用性（前端据此显示开关；密钥是否配置只返回布尔，不外泄）。"""
    return {"ok": bool(dashscope_key()), "models": OMNI_MODELS, "voices": OMNI_VOICES,
            "default_model": omni_mod.model_id(), "default_voice": omni_mod.voice_id(),
            "sample_rate": omni_mod.SAMPLE_RATE}


@app.post("/api/rebut-omni")
async def rebut_omni(request: Request):
    """端到端出稿：文本入 → 字幕 + PCM 音频流式出（一步到位，省掉独立 TTS）。

    SSE 事件：{"type":"text","delta":..} / {"type":"audio","delta":<base64 PCM16 24k>}
             / {"type":"done",..} / {"type":"error","message":..}
    """
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    params = _validate(body)
    memory = str(body.get("memory") or "")
    voice = str(body.get("voice") or "").strip()
    model = str(body.get("model") or "").strip()

    async def gen():
        yield ": connected\n\n"
        async for event in omni_mod.rebut_stream_omni(
                **params, memory=memory, voice=voice, model=model):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------- 对话模式（正方版）----------------

@app.post("/api/chat")
async def chat(request: Request):
    """对话模式：一次问答。

    body: {messages:[{role,content}], topic, notes[], prefs[], topics[],
           mode:"omni"|"cascade", model, voice, system_extra}
    端到端 → 字幕 + PCM 音频；级联 → 仅字幕（前端再调 /api/tts-stream 播报）
    """
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    messages = chat_mod.norm_messages(body.get("messages"))
    if not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    topic = str(body.get("topic") or "")[:200]
    notes = body.get("notes") or []
    prefs = body.get("prefs") or []
    topics = body.get("topics") or []
    extra = str(body.get("system_extra") or "")[:3000]
    voice = str(body.get("voice") or "").strip()
    model = str(body.get("model") or "").strip()
    mode = str(body.get("mode") or "").strip().lower()
    with_materials = body.get("materials") is not False   # 对话模式默认启用队内资料
    if mode not in ("omni", "cascade"):
        mode = "omni" if dashscope_key() else "cascade"
    # ★上下文来源（前端勾选）
    unsum = chat_mod.norm_messages(body.get("unsummarized"), limit=24)
    use_full = bool(body.get("use_full"))
    use_memory = body.get("use_memory") is not False
    use_distill = bool(body.get("use_distill"))
    distill = str(body.get("distill") or "")[:6000]

    async def gen():
        yield ": connected\n\n"
        if mode == "omni":
            agen = chat_mod.chat_stream_omni(
                messages=messages, topic=topic, notes=notes, prefs=prefs, topics=topics,
                voice=voice, model=model, extra=extra,
                unsummarized=unsum, use_full=use_full, use_memory=use_memory,
                use_distill=use_distill, distill=distill,
                with_materials=with_materials)
        else:
            client = engine.client_for(model or "glm-5.3", "rebut")
            agen = chat_mod.chat_stream_cascade(
                client=client, messages=messages, topic=topic, notes=notes,
                prefs=prefs, topics=topics, extra=extra,
                unsummarized=unsum, use_full=use_full, use_memory=use_memory,
                use_distill=use_distill, distill=distill,
                with_materials=with_materials)
        async for event in agen:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/chat-summary")
async def chat_summary(request: Request):
    """把一段讨论整理成「资料 notes / 偏好 prefs / 话题 topics」。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    messages = chat_mod.norm_messages(body.get("messages"), limit=40)
    if not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    prev = body.get("prev") or {}
    model = str(body.get("model") or "glm-5.3").strip()
    client = engine.client_for(model, "summary")
    try:
        res = await chat_mod.summarize_discussion(client=client, messages=messages, prev=prev)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"总结失败：{type(exc).__name__}: {exc}"[:300])
    return res


# ---------------- 辩论队内资料库（赛前讨论/质询训练/教练复盘的提炼）----------------

@app.get("/api/materials")
async def materials_list_api():
    """资料库清单 + 当前注入字数（brief 为真正注入提示词的那一份）。"""
    return {"enabled": materials_mod.enabled(),
            "brief_name": materials_mod.BRIEF_NAME,
            "brief_chars": len(materials_mod.brief()),
            "items": materials_mod.list_materials()}


@app.get("/api/materials/{name}")
async def materials_get_api(name: str):
    txt = materials_mod.read_material(name, 60000)
    if not txt:
        raise HTTPException(status_code=404, detail="资料不存在")
    return {"name": name, "chars": len(txt), "text": txt}


@app.post("/api/materials")
async def materials_save_api(request: Request):
    """新增/覆盖一份资料（name 以 .md 结尾，自动补）。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    name = str(body.get("name") or "").strip()
    text = str(body.get("text") or "")
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    return {"ok": True, **materials_mod.save_material(name, text)}


@app.delete("/api/materials/{name}")
async def materials_delete_api(name: str, request: Request):
    _auth(request)
    return {"ok": materials_mod.delete_material(name)}


# ---------------- 对话数据云端存储（落盘 /data/chat，容器重建不丢）----------------

@app.post("/api/chat-store/save")
async def chat_store_save(request: Request):
    """部分更新：messages / memory / distill 三者只需传要改的那个。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    sid = str(body.get("sid") or "").strip()
    try:
        data = chatstore.save(sid, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "sid": sid, "updatedAt": data.get("updatedAt"),
            "messages": len(data.get("messages") or []),
            "notes": len((data.get("memory") or {}).get("notes") or [])}


@app.get("/api/chat-store/get")
async def chat_store_get(sid: str = ""):
    try:
        return chatstore.load(sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/chat-store/list")
async def chat_store_list(limit: int = 50):
    return {"sessions": chatstore.list_sessions(limit)}


@app.post("/api/chat-store/delete")
async def chat_store_delete(request: Request):
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    sid = str(body.get("sid") or "")
    try:
        return {"ok": chatstore.delete(sid), "sid": sid}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/quick")
async def quick(request: Request):
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    question = str(body.get("question") or "").strip()
    our_side = str(body.get("our_side") or "").strip()
    our_case = str(body.get("our_case") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="追问不能为空")
    try:
        result = await engine.quick(question=question, our_side=our_side, our_case=our_case)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result


@app.post("/api/asr")
async def asr_api(request: Request):
    """接收一段音频原始字节，返回识别文本（服务端调用在线 ASR，密钥不出服务器）。

    ?model=<id> 可指定识别模型（见 /api/asr-models），非法值回退默认。
    """
    _auth(request)
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="音频过大（单段 ≤20MB）")
    ctype = (request.headers.get("content-type") or "").lower()
    ext = "wav"
    if "webm" in ctype:
        ext = "webm"
    elif "ogg" in ctype:
        ext = "ogg"
    elif "mp3" in ctype or "mpeg" in ctype:
        ext = "mp3"
    elif "mp4" in ctype or "m4a" in ctype or "aac" in ctype:
        ext = "m4a"
    model = request.query_params.get("model") or ""
    ctx = (request.query_params.get("ctx") or "").strip()     # 上文提示：辩题 + 上一段识别原文
    fix = request.query_params.get("fix") in ("1", "true", "on")
    try:
        text = await asr_mod.transcribe(raw, filename=f"seg.{ext}",
                                        content_type=ctype or "audio/wav", model=model,
                                        prompt=ctx)
    except asr_mod.AsrError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    raw = text
    if fix and text:
        try:
            text = await asr_mod.polish(text, engine.fast)
        except Exception:  # noqa: BLE001 修正失败不影响识别结果
            pass
    out = {"text": text, "raw": raw, "fixed": bool(fix)}
    segs = asr_mod.parse_diarize(raw)
    if segs:
        out["segments"] = segs      # 说话人分离模型：附带「说话人编号 → 文本」分段，供前端归属
    return out


@app.post("/api/polish")
async def polish_api(request: Request):
    """只对一段「新增文本」做识别纠错。

    客户端先按识别原文去重（去掉与上一段的重叠），再只把新增部分送来修正，
    这样修正不会篡改重叠区、也不会让去重失效。
    """
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="文本过长（≤2000 字）")
    fixed = await asr_mod.polish(text, engine.fast)
    return {"text": fixed}


@app.post("/api/tts")
async def tts_api(request: Request):
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    # 收尾规范化：无结束标点时补一个，避免合成器在句尾吞音
    if text[-1] not in "。！？!?…；;":
        text = text + "。"
    if len(text) > 300:
        raise HTTPException(status_code=400, detail="单句文本过长（≤300 字）")
    voice = str(body.get("voice") or tts_voice())
    rate = str(body.get("rate") or tts_rate())
    tts_model_name = str(body.get("model") or "")
    emotion = str(body.get("emotion") or "")
    try:
        audio = await tts_mod.synthesize(text, voice, rate, tts_model_name, emotion)
    except Exception as exc:  # noqa: BLE001 对外统一提示
        raise HTTPException(status_code=502, detail=f"语音合成失败：{exc}")
    media = "audio/wav" if audio[:4] == b"RIFF" else "audio/mpeg"
    return Response(content=audio, media_type=media,
                    headers={"Cache-Control": "no-store"})


@app.post("/api/tts-stream")
async def tts_stream_api(request: Request):
    """流式语音合成：上游边合成边下发 WAV，这里边收边转发，供前端「边收边播」。

    前端拿到 WAV 字节流后按 RIFF 解析出 PCM（24kHz / 16bit / 单声道），
    用 Web Audio 排队播放，消除「整句合成完才开始响」的等待。
    """
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    if text[-1] not in "。！？!?…；;":
        text = text + "。"
    if len(text) > 300:
        raise HTTPException(status_code=400, detail="单句文本过长（≤300 字）")
    voice = str(body.get("voice") or tts_voice())
    rate = str(body.get("rate") or tts_rate())
    tts_model_name = str(body.get("model") or "")
    emotion = str(body.get("emotion") or "")

    async def gen():
        try:
            async for chunk in tts_mod.stream_wav(text, voice, rate, tts_model_name, emotion):
                yield chunk
        except Exception as exc:  # noqa: BLE001 响应已开始，无法改状态码，仅留痕
            print("[debate] tts-stream 中断:", exc, flush=True)

    return StreamingResponse(gen(), media_type="audio/wav",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/factcheck")
async def factcheck_api(request: Request):
    """独立事实核查：对给定句子逐句检查不可验证的数据/引用/绝对化断言。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    raw_sentences = body.get("sentences")
    if isinstance(raw_sentences, str):
        raw_sentences = [s for s in raw_sentences.split("\n") if s.strip()]
    if not isinstance(raw_sentences, list):
        raise HTTPException(status_code=400, detail="sentences 应为数组或换行文本")
    sentences = [str(s).strip() for s in raw_sentences if str(s).strip()]
    if not sentences:
        raise HTTPException(status_code=400, detail="句子列表为空")
    if len(sentences) > 60:
        raise HTTPException(status_code=400, detail="句子过多（≤60）")
    hits = await factcheck_mod.check(engine.fast, sentences)
    return {"hits": hits, "checked": len(sentences), "dropped": len(hits)}


@app.post("/api/feedback")
async def feedback(request: Request):
    """记录验收指标：可采纳率打分（1-5）、打断响应时延等。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

    score = body.get("score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    if score is not None and not 1 <= score <= 5:
        raise HTTPException(status_code=400, detail="评分须在 1-5 之间")
    interrupt_ms = body.get("interrupt_ms")
    try:
        interrupt_ms = int(interrupt_ms) if interrupt_ms is not None else None
    except (TypeError, ValueError):
        interrupt_ms = None
    metrics_mod.record_feedback(score=score, interrupt_ms=interrupt_ms,
                                note=str(body.get("note") or ""),
                                mode=str(body.get("mode") or engine.mode))
    return {"ok": True, "summary": metrics_mod.summary()}


@app.post("/api/attribute")
async def attribute_api(request: Request):
    """身份归属：判断每段发言的来源（opp 对方 / ours 我方 / hall 会场）。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    raw = body.get("texts")
    if isinstance(raw, str):
        raw = [s for s in raw.split("\n") if s.strip()]
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="texts 应为数组或换行文本")
    texts = [str(s).strip() for s in raw if str(s).strip()]
    if not texts:
        raise HTTPException(status_code=400, detail="待判定文本为空")
    if len(texts) > 30:
        raise HTTPException(status_code=400, detail="单次判定过多（≤30）")
    ctx = body.get("context")
    ctx = [str(c).strip() for c in ctx] if isinstance(ctx, list) else []
    judge = engine.client_for(str(body.get("model") or ""), "judge")   # 实时判定槽位
    _split = body.get("split")
    _split = True if _split is None else bool(_split)
    items = await conductor.attribute(judge, texts,
                                      our_side=str(body.get("our_side") or ""),
                                      topic=str(body.get("topic") or ""),
                                      context=ctx, split=_split)
    return {"items": items}


@app.post("/api/should-speak")
async def should_speak_api(request: Request):
    """自主判断时机：当前是否轮到我方二辩发言。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    recent = body.get("recent")
    if isinstance(recent, str):
        recent = [s for s in recent.split("\n") if s.strip()]
    if not isinstance(recent, list):
        raise HTTPException(status_code=400, detail="recent 应为数组或换行文本")
    recent = [str(s).strip() for s in recent if str(s).strip()]
    gap_ms = body.get("gap_ms")
    try:
        gap_ms = int(gap_ms) if gap_ms is not None else None
    except (TypeError, ValueError):
        gap_ms = None
    tail_complete = body.get("tail_complete")
    if not isinstance(tail_complete, bool):
        tail_complete = None
    judge = engine.client_for(str(body.get("model") or ""), "judge")   # 实时判定槽位
    result = await conductor.should_speak(judge, recent,
                                          our_side=str(body.get("our_side") or ""),
                                          gap_ms=gap_ms, tail_complete=tail_complete)
    return result


@app.post("/api/summarize")
async def summarize_api(request: Request):
    """长期总结（滚动摘要）：把「已有摘要 + 新增转写」合并成长期记忆，供出稿时注入。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    prev = body.get("prev")
    if not isinstance(prev, dict):
        prev = None
    recent = body.get("recent")
    if isinstance(recent, str):
        recent = [s for s in recent.split("\n") if s.strip()]
    if not isinstance(recent, list):
        recent = []
    recent = [str(s).strip() for s in recent if str(s).strip()]
    if not recent and not prev:
        raise HTTPException(status_code=400, detail="prev 与 recent 不能同时为空")
    try:
        return await engine.summarize(prev=prev, recent=recent,
                                      model_id=str(body.get("model") or ""))
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/transcript-save")
async def transcript_save(request: Request):
    """全自动模式：把识别到的发言（含来源身份）持久化到服务器。"""
    _auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    session = str(body.get("session") or "default").strip()
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items 应为数组")
    if len(items) > 200:
        raise HTTPException(status_code=400, detail="单次写入过多（≤200）")
    saved = store.append(session, items)
    return {"ok": True, "saved": saved}


@app.get("/api/transcript-list")
async def transcript_list(session: str = "default", limit: int = 200):
    """读取某个会话已保存的发言记录。"""
    return {"session": session, "items": store.load(session, limit)}


@app.get("/api/transcript-sessions")
async def transcript_sessions():
    """列出已保存的会话。"""
    return {"sessions": store.sessions()}


@app.get("/api/stats")
async def stats():
    """M3 验收指标汇总 + 引擎路由信息。"""
    return {"metrics": metrics_mod.summary(), "engine": engine.describe(),
            "transcripts": store.sessions()}


@app.get("/")
async def index():
    return FileResponse(str(WEB_DIR / "index.html"))


# ── 实时 ASR 中转（备用可选通道，默认关闭）────────────────────────────────────
# 浏览器无法在 WebSocket 握手中自带 Authorization 头，而百炼实时识别要求 Bearer 鉴权，
# 因此必须由本服务中转：浏览器 ←WS→ 本服务 ←WS→ 百炼 Qwen3-ASR-Realtime。
# 定位：仅作为「可选方案」。开启后其增量文本只用于「流式抢跑」的更早启动，
# 正式转写仍走原有分片链路——以保住说话人分离（realtime 版不支持分离）。
RT_ASR_MODEL = os.environ.get("DEBATE_RT_ASR_MODEL", "qwen3-asr-flash-realtime")
RT_ASR_BASE = os.environ.get("DEBATE_RT_ASR_URL",
                             "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")


def _ws_header_kwarg() -> str:
    """websockets 12/13 用 extra_headers；14+ 改名 additional_headers。"""
    try:
        import inspect
        import websockets
        params = inspect.signature(websockets.connect).parameters
        return "additional_headers" if "additional_headers" in params else "extra_headers"
    except Exception:
        return "extra_headers"


@app.websocket("/api/asr-realtime")
async def asr_realtime(ws: WebSocket):
    await ws.accept()
    key = dashscope_key()
    if not key:
        await ws.close(code=1011, reason="missing DASHSCOPE_API_KEY")
        return
    try:
        import websockets
    except Exception as e:                                   # pragma: no cover
        await ws.close(code=1011, reason=f"websockets unavailable: {e}")
        return
    url = f"{RT_ASR_BASE}?model={RT_ASR_MODEL}"
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Beta": "realtime=v1"}
    kwargs = {_ws_header_kwarg(): headers}
    try:
        async with websockets.connect(url, max_size=None, ping_interval=20,
                                      ping_timeout=20, close_timeout=3,
                                      **kwargs) as up:
            async def down():                               # 百炼 → 浏览器
                async for msg in up:
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    else:
                        await ws.send_text(msg)

            async def upf():                                # 浏览器 → 百炼
                while True:
                    ev = await ws.receive()
                    if ev.get("type") == "websocket.disconnect":
                        return
                    if ev.get("text") is not None:
                        await up.send(ev["text"])
                    elif ev.get("bytes") is not None:
                        await up.send(ev["bytes"])

            t1 = asyncio.create_task(down())
            t2 = asyncio.create_task(upf())
            _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": str(e)[:200]}))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------- 大屏联动：发言事件总线（谁在说话 → 大屏亮灯/脉冲）----------------
# 智能体前端在「开始播报 / 播报结束」时 POST /api/stage/publish，
# 大屏页（/debate/time/）用 EventSource 订阅 /api/stage/stream，实时显示「正在发言」。
# 走服务端中转而不是 BroadcastChannel：大屏与智能体通常在两台电脑上。
_stage_clients: set[asyncio.Queue] = set()
_stage_last: dict = {}
STAGE_TTL_SEC = 90          # 「正在发言」状态的有效期：超过这么久没再收到事件视为已结束（防止残留）


@app.get("/api/stage/stream")
async def stage_stream(request: Request):
    """大屏订阅通道（SSE）。断线由浏览器自动重连；连上先补发最近一条状态。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _stage_clients.add(q)

    async def gen():
        yield ": connected\n\n"
        try:
            # 只补发「仍未过期」的状态：避免比赛前打开大屏时，看到上次残留的“正在发言”
            if _stage_last and (time.time() - float(_stage_last.get("ts") or 0)) < STAGE_TTL_SEC:
                yield f"data: {json.dumps(_stage_last, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=10)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _stage_clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/stage/publish")
async def stage_publish(request: Request):
    """智能体推送发言事件。body: {type, side, seat, on, who, stage}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    ev = {k: body[k] for k in ("type", "side", "seat", "on", "who", "stage") if k in body}
    if not ev.get("type"):
        raise HTTPException(status_code=400, detail="缺少 type")
    ev["ts"] = time.time()
    _stage_last.clear(); _stage_last.update(ev)
    n = 0
    for q in list(_stage_clients):
        try:
            q.put_nowait(ev); n += 1
        except asyncio.QueueFull:
            pass
    return {"ok": True, "clients": n}


@app.get("/api/stage/state")
async def stage_state():
    """调试用：当前订阅数 + 最近一条事件。"""
    return {"ok": True, "clients": len(_stage_clients), "last": _stage_last}


# ---------------- 实体发令按钮总线（抢答器 → 智能体：「发言结束」信号）----------------
# 现场用一块 ESP32 接两个抢答按钮（A=正方侧 / B=反方侧），插在大屏那台电脑上。
#   · 同学发言结束 → 按下本侧按钮 → 大屏 POST /api/buzzer{speer} → 智能体立刻开口
#   · 智能体发言结束 → 自己 POST /api/buzzer{source:"ai"} → 大屏/对手侧收到「该你了」
# 这条链路绕开了所有判停逻辑：按下即发令，不再等静默、不再调 should-speak、不看冷却。
_buzz_clients: set[asyncio.Queue] = set()
_buzz_last: dict = {}
# ── 事件序号 + 短历史：供 WiFi 按钮板「长轮询」拿下行指令（蜂鸣器提示）──
_buzz_seq: int = 0
_buzz_history: list[dict] = []          # 最近 80 条，带 seq
BUZZ_HISTORY_MAX = 80


def _norm_side(v) -> str:
    s = str(v or "").strip()
    if s in ("A", "a", "pro", "正方"):
        return "正方"
    if s in ("B", "b", "con", "反方"):
        return "反方"
    return s


@app.get("/api/buzzer/stream")
async def buzzer_stream(request: Request):
    """订阅「发言结束」发令（智能体前端 / 大屏 都可订阅）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _buzz_clients.add(q)

    async def gen():
        yield ": connected\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=10)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _buzz_clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/buzzer")
async def buzzer_publish(request: Request):
    """发布一条发令。body: {side:"正方"|"反方"|"A"|"B", action:"down"|"up"|"ai_done",
       source:"button"|"ai"|"keyboard", seat?, note?}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    ev = {
        "side": _norm_side(body.get("side")),
        "action": str(body.get("action") or "down"),
        "source": str(body.get("source") or "button"),
    }
    for k in ("seat", "note", "kind", "reason", "board", "name", "floor"):
        if body.get(k) is not None:
            ev[k] = str(body[k])[:120]
    # ★ stage 必须保持数字：前端用 typeof === "number" 判断环节号，字符串化会让它永久停在 -1
    #   （症状：任何环节都被判定「可以发言」，人机对辩以外的环节也可能插话）
    if isinstance(body.get("stage"), (int, float)) and not isinstance(body.get("stage"), bool):
        ev["stage"] = int(body["stage"])
    # ★ target 是人机对辩「轮到哪一方上场」的唯一判据，丢了会让 AI 在别人被提问时插话
    if isinstance(body.get("target"), dict):
        ev["target"] = {k: body["target"].get(k) for k in ("side", "seat")
                        if body["target"].get(k) is not None}
    ev["ts"] = time.time()
    global _buzz_seq
    _buzz_seq += 1
    ev["seq"] = _buzz_seq
    _buzz_history.append(ev)
    if len(_buzz_history) > BUZZ_HISTORY_MAX:
        del _buzz_history[:len(_buzz_history) - BUZZ_HISTORY_MAX]
    _buzz_last.clear(); _buzz_last.update(ev)
    n = 0
    for q in list(_buzz_clients):
        try:
            q.put_nowait(ev); n += 1
        except asyncio.QueueFull:
            pass
    return {"ok": True, "clients": n, "event": ev}


@app.get("/api/buzzer/state")
async def buzzer_state():
    return {"ok": True, "clients": len(_buzz_clients), "last": _buzz_last, "seq": _buzz_seq}


@app.get("/api/buzzer/poll")
async def buzzer_poll(after: int = 0, board: str = "", timeout: float = 20.0):
    """WiFi 按钮板长轮询下行指令（ESP32 用；不占 SSE 通道）。

    - after: 板子最后收到的事件序号，只返回 seq > after 的事件
    - timeout: 最长挂起秒数（默认 20s，建议板子略大于此值）
    返回 {"ok":true,"seq":最新,"events":[...]}；events 只含「非本板发出」的事件，
    供板子判断要不要响蜂鸣器（例如 source=ai / action=ai_done → 提示该按按钮了）。
    """
    deadline = time.time() + max(0.0, min(30.0, timeout))
    me = str(board or "").strip().upper()
    while True:
        evs = []
        cur = 0
        for e in _buzz_history:
            if int(e.get("seq") or 0) <= after:
                continue
            cur = max(cur, int(e.get("seq") or 0))
            src_board = str(e.get("board") or "").upper()
            if me and src_board and src_board == me:
                continue                      # 自己发出去的，不通知自己
            evs.append(e)
        if evs:
            return {"ok": True, "seq": cur or _buzz_seq, "events": evs}
        if time.time() >= deadline:
            return {"ok": True, "seq": _buzz_seq, "events": []}
        await asyncio.sleep(0.15)


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

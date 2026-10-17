"""探测百炼 Omni Realtime：分块推流 + 首字延迟 + 出稿质量（结构要求遵循度）。"""
import asyncio, base64, json, os, sys, time, wave

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MODELS = ["qwen3.8-omni-flash-realtime", "qwen3.5-omni-plus-realtime"]

# 模拟我们真实的出稿要求（ASSESSMENT 精简版），检验端到端模型能否守住结构
SYS = (
    "你是中学生辩论赛的正方二辩。请针对对方发言做反驳，要求："
    "①先拆解对方的核心前提与适用条件，再反驳；"
    "②指出逻辑跳跃、以偏概全、因果倒置等问题（用日常语言，不报术语）；"
    "③论点按「现象→原因→价值」三层推进，至少推到第二层；"
    "④必须至少包含一个明确的设问句（例如「那么请问对方，……？」）；"
    "⑤口语化，每句 20~45 字，直接可播，不要说「好的」「下面我来说」。"
)
OPP = ("对方说人工智能会替代人的工作，所以人的价值被消解。而且技术越进步，人越没用。"
       "历史上每次技术革命都淘汰了大量劳动力，这次人工智能不一样，它连脑力劳动都能替代。")


async def run(model: str, pcm: bytes, chunk_ms: int = 100):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    t0 = time.time()
    ws = await websockets.connect(f"{BASE}?model={model}", **{kw: hdr}, max_size=None, ping_interval=20)
    first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    out = {"model": model, "conn_ms": int((time.time() - t0) * 1000),
           "session": first.get("type"), "server_model": (first.get("session") or {}).get("model")}
    await ws.send(json.dumps({"type": "session.update", "session": {
        "modalities": ["text"], "input_audio_format": "pcm16",
        "instructions": SYS,
        "input_audio_transcription": {"model": "qwen3-asr-flash-realtime"},
        "turn_detection": {"type": "server_vad", "silence_duration_ms": 600},
    }}))
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ev.get("type") == "session.updated":
            break
        if ev.get("type") == "error":
            out["session_error"] = json.dumps(ev, ensure_ascii=False)[:300]; break

    # 分块推流（100ms = 3200B @16k/16bit/单声道）
    step = int(16000 * 2 * chunk_ms / 1000)
    t_send = time.time()
    for i in range(0, len(pcm), step):
        await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                  "audio": base64.b64encode(pcm[i:i + step]).decode()}))
        await asyncio.sleep(chunk_ms / 1000 * 0.9)
    out["push_s"] = round(time.time() - t_send, 2)
    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

    text, t_tx, t_first_delta, t_done = "", None, None, None
    vad = {}
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        if et.endswith("transcription.completed"):
            t_tx = time.time() - t_send
            out["heard"] = str(ev.get("transcription"))[:200]
        if "speech_started" in et: vad["start"] = round(time.time() - t_send, 2)
        if "speech_stopped" in et: vad["stop"] = round(time.time() - t_send, 2)
        if et.endswith("text.delta"):
            d = ev.get("delta") or ""
            if d and t_first_delta is None: t_first_delta = time.time() - t_send
            text += d
        if et.endswith("audio.delta") and "first_audio" not in out:
            out["first_audio_s"] = round(time.time() - t_send, 2)
        if et in ("response.done", "response.completed"):
            t_done = time.time() - t_send; break
        if et == "error":
            out["error"] = json.dumps(ev, ensure_ascii=False)[:300]; break
    out.update(vad=vad, heard_s=round(t_tx, 2) if t_tx else None,
               first_delta_s=round(t_first_delta, 2) if t_first_delta else None,
               done_s=round(t_done, 2) if t_done else None, len=len(text), text=text[:500])
    try: await ws.close()
    except Exception: pass
    return out


if __name__ == "__main__":
    if not KEY: print("NO_KEY"); sys.exit(1)
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/t16k.wav"
    with wave.open(path, "rb") as w:
        pcm = w.readframes(min(w.getnframes(), 16000 * 18))
    for m in MODELS:
        try:
            print(json.dumps(asyncio.run(run(m, pcm)), ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"model": m, "fail": f"{type(e).__name__}: {e}"[:200]}, ensure_ascii=False))
        time.sleep(2)

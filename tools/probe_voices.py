"""探测 Omni Realtime 可用音色 + 输出采样率。"""
import asyncio, base64, json, os, sys, time

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
RT = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MODEL = "qwen3.5-omni-plus-realtime"
CANDIDATES = ["Tina", "Cherry", "Serena", "Ethan", "Chelsie", "Jada", "Sunny",
              "Dylan", "Nofish", "Peter", "Rocky", "Kiki", "Elias", "Li", "Marcus", "Roy"]
TXT = "我方认为，人工智能拓展了人的价值。技术替我们做重复劳动，人才能去做更有创造性的事。"


async def probe_voice(voice: str):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    try:
        ws = await websockets.connect(f"{RT}?model={MODEL}", **{kw: hdr}, max_size=None)
    except Exception as e:
        return {"voice": voice, "conn": f"FAIL {type(e).__name__}"}
    await asyncio.wait_for(ws.recv(), timeout=10)
    await ws.send(json.dumps({"type": "session.update", "session": {
        "modalities": ["text", "audio"], "voice": voice,
        "instructions": "你是中学生辩手，口语化，简短。",
        "turn_detection": None, "output_audio_format": "pcm"}}))
    err = None
    while True:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        except asyncio.TimeoutError:
            break
        if ev.get("type") == "session.updated":
            break
        if ev.get("type") == "error":
            err = json.dumps(ev, ensure_ascii=False)[:160]; break
    if err:
        try: await ws.close()
        except Exception: pass
        return {"voice": voice, "ok": False, "err": err}
    t0 = time.time()
    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "请用一句话说：" + TXT}]}}))
    await ws.send(json.dumps({"type": "response.create", "response": {"modalities": ["text", "audio"]}}))
    t_audio, chunks, chars, tr = None, [], 0, ""
    while time.time() - t0 < 120:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        if et.endswith("audio.delta"):
            if t_audio is None: t_audio = time.time() - t0
            chunks.append(base64.b64decode(ev.get("delta") or ""))
        if et.endswith("audio_transcript.delta"): tr += ev.get("delta") or ""
        if et in ("response.done", "response.completed"): break
        if et == "error": break
    try: await ws.close()
    except Exception: pass
    data = b"".join(chunks)
    # 采样率推断：中文字速约 4.5 字/秒；bytes/秒 = sr*2
    secs = len(tr) / 4.5 if tr else 0
    sr = int(len(data) / secs / 2) if secs and data else 0
    out = "/tmp/voice_%s.pcm" % voice
    if data: open(out, "wb").write(data)
    return {"voice": voice, "ok": True, "first_audio_s": round(t_audio, 2) if t_audio else None,
            "bytes": len(data), "transcript": tr, "sr_est": sr}


if __name__ == "__main__":
    for v in CANDIDATES:
        try:
            print(json.dumps(asyncio.run(probe_voice(v)), ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"voice": v, "fail": f"{type(e).__name__}: {e}"[:150]}, ensure_ascii=False))
        time.sleep(0.8)

"""为 4 个候选音色各生成一段相同的辩论发言，导出 wav 供试听挑选。"""
import asyncio, base64, json, os, sys, time, wave

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
RT = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MODEL = "qwen3.5-omni-plus-realtime"
VOICES = ["Tina", "Serena", "Ethan", "Dylan"]
TEXT = ("对方把「替代岗位」直接当成了「消解价值」，这两件事差得很远。"
        "请问对方，计算器取代了算盘，数学家就没价值了吗？"
        "人工智能接手重复劳动，恰恰是把人推去做更需要判断和创造的事。")
SYS = "你是中学生辩论赛的正方二辩，口语自然、语气坚定但不喊口号，语速正常。"


async def gen(voice):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    ws = await websockets.connect(f"{RT}?model={MODEL}", **{kw: hdr}, max_size=None)
    await asyncio.wait_for(ws.recv(), timeout=10)
    await ws.send(json.dumps({"type": "session.update", "session": {
        "modalities": ["text", "audio"], "voice": voice, "instructions": SYS,
        "output_audio_format": "pcm", "turn_detection": None}}))
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ev.get("type") == "session.updated": break
        if ev.get("type") == "error": return None
    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": "请直接朗读下面这段话（不要加任何前后缀）：" + TEXT}]}}))
    await ws.send(json.dumps({"type": "response.create", "response": {"modalities": ["text", "audio"]}}))
    data = bytearray(); t0 = time.time()
    while time.time() - t0 < 120:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        if et.endswith("audio.delta"):
            data.extend(base64.b64decode(ev.get("delta") or ""))
        if et in ("response.done", "response.completed"): break
        if et == "error": break
    try: await ws.close()
    except Exception: pass
    return bytes(data)


if __name__ == "__main__":
    for v in VOICES:
        try:
            pcm = asyncio.run(gen(v))
        except Exception as e:
            print(v, "FAIL", e); continue
        if not pcm:
            print(v, "EMPTY"); continue
        path = f"/tmp/say_{v}.wav"
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(pcm)
        print(v, "OK", len(pcm), "bytes", round(len(pcm) / 48000, 1), "s")
        time.sleep(1)

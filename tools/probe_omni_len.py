"""确认音频输出格式 + 限长版效果（发言时长约束）。"""
import asyncio, json, os, sys, time

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
RT = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
try:
    sys.path.insert(0, "/app")
    from debate import prompts
    BASE_SYS = prompts.SYSTEM
except Exception:
    BASE_SYS = "你是中学生辩论赛的正方二辩。"

LIMIT = ("\n\n【本次发言时长约束】人机对辩每轮只有 40~60 秒，请把整段发言控制在 260~330 字，"
         "最多 3 个论点，说完即止，不要展开成全面论述。")

OPP = ("对方说人工智能会替代人的工作，所以人的价值被消解。而且技术越进步，人越没用。"
       "历史上每次技术革命都淘汰了大量劳动力，这次人工智能不一样，它连脑力劳动都能替代。")


async def run(model, sys_prompt, modalities=("text",), voice=None, show_session=False):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    ws = await websockets.connect(f"{RT}?model={model}", **{kw: hdr}, max_size=None)
    out = {}
    await asyncio.wait_for(ws.recv(), timeout=10)
    sess = {"modalities": list(modalities), "instructions": sys_prompt, "turn_detection": None}
    if voice: sess["voice"] = voice
    await ws.send(json.dumps({"type": "session.update", "session": sess}))
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ev.get("type") == "session.updated":
            s = ev.get("session") or {}
            if show_session:
                out["session_echo"] = {k: s.get(k) for k in
                    ("modalities", "voice", "output_audio_format", "input_audio_format")}
            break
        if ev.get("type") == "error":
            return {"err": json.dumps(ev, ensure_ascii=False)[:300]}
    t0 = time.time()
    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": OPP}]}}))
    await ws.send(json.dumps({"type": "response.create", "response": {"modalities": list(modalities)}}))
    text, t_first_text, t_first_audio, ab, ev_types = "", None, None, 0, []
    while time.time() - t0 < 180:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        if len(ev_types) < 14: ev_types.append(et)
        if et.endswith("text.delta"):
            d = ev.get("delta") or ""
            if d and t_first_text is None: t_first_text = time.time() - t0
            text += d
        if et.endswith("audio.delta"):
            if t_first_audio is None: t_first_audio = time.time() - t0
            ab += len(ev.get("delta") or "")
        if et in ("response.done", "response.completed"): break
        if et == "error": return {"err": json.dumps(ev, ensure_ascii=False)[:300]}
    try: await ws.close()
    except Exception: pass
    out.update(first_text_s=round(t_first_text, 2) if t_first_text else None,
               first_audio_s=round(t_first_audio, 2) if t_first_audio else None,
               total_s=round(time.time() - t0, 2), chars=len(text),
               audio_sec_est=round(ab * 0.75 / 48000, 1),  # 24k/16bit/mono 估算秒数
               ev=ev_types,
               text=text[:400])
    return out


if __name__ == "__main__":
    M = "qwen3.5-omni-plus-realtime"
    print("== A. 限长版（260~330 字，纯文本）==")
    for i in range(2):
        r = asyncio.run(run(M, BASE_SYS + LIMIT))
        print(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False))
        print("   T:", r.get("text", "")[:300].replace("\n", " "))
    print("== B. 限长版 + 音频输出（探测格式）==")
    r = asyncio.run(run(M, BASE_SYS + LIMIT, ("text", "audio"), voice="Tina", show_session=True))
    print(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False))

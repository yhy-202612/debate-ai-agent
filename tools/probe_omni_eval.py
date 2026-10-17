"""端到端可行性严格评估：
1) Omni + 我们完整 SYSTEM（技法库+考察口径）→ 跑 3 次看稳定性
2) deepseek-flash 同题对照（我们现网的实际水平与耗时）
3) Omni 音频输出首包延迟 + 音色可用性
"""
import asyncio, json, os, sys, time

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
RT_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DS_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DS_MODEL = os.environ.get("DEBATE_DEEPSEEK_MODEL", "deepseek-flash")

try:
    sys.path.insert(0, "/app")
    from debate import prompts
    SYSTEM = prompts.SYSTEM
except Exception as e:
    SYSTEM = "你是中学生辩论赛的正方二辩。"
    print("[warn] prompts 读取失败:", e, file=sys.stderr)

OPP = ("对方说人工智能会替代人的工作，所以人的价值被消解。而且技术越进步，人越没用。"
       "历史上每次技术革命都淘汰了大量劳动力，这次人工智能不一样，它连脑力劳动都能替代。")


def quality(text: str) -> dict:
    import re
    q = len(re.findall(r"[？?]", text))
    sents = [s for s in re.split(r"(?<=[。！？…])", text) if s.strip()]
    avg = round(sum(len(s) for s in sents) / len(sents), 1) if sents else 0
    # 「三层」（现象/原因/价值语义簇）
    layers = sum(1 for k in ["现象", "原因", "价值", "本质", "根源", "说到底"] if k in text)
    attack = sum(1 for k in ["前提", "假设", "如果", "难道", "是不是", "请问", "反过来"] if k in text)
    return {"设问": q, "句数": len(sents), "均长": avg, "三层命中": layers, "攻击性": attack}


async def omni_once(model: str, modalities=("text",), voice=None):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    ws = await websockets.connect(f"{RT_BASE}?model={model}", **{kw: hdr}, max_size=None)
    await asyncio.wait_for(ws.recv(), timeout=10)
    sess = {"modalities": list(modalities), "instructions": SYSTEM, "turn_detection": None}
    if voice: sess["voice"] = voice
    await ws.send(json.dumps({"type": "session.update", "session": sess}))
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ev.get("type") == "session.updated": break
        if ev.get("type") == "error": return {"err": json.dumps(ev, ensure_ascii=False)[:300]}
    t0 = time.time()
    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": OPP}]}}))
    await ws.send(json.dumps({"type": "response.create", "response": {"modalities": list(modalities)}}))
    text, t_first, t_audio, audio_bytes = "", None, None, 0
    while time.time() - t0 < 120:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        if et.endswith("text.delta"):
            d = ev.get("delta") or ""
            if d and t_first is None: t_first = time.time() - t0
            text += d
        if et.endswith("audio.delta"):
            if t_audio is None: t_audio = time.time() - t0
            audio_bytes += len(ev.get("delta") or "")
        if et in ("response.done", "response.completed"): break
        if et == "error": return {"err": json.dumps(ev, ensure_ascii=False)[:300]}
    try: await ws.close()
    except Exception: pass
    return {"first_text_s": round(t_first, 2) if t_first else None,
            "first_audio_s": round(t_audio, 2) if t_audio else None,
            "total_s": round(time.time() - t0, 2), "len": len(text),
            "audio_kb": round(audio_bytes * 0.75 / 1024, 1), "text": text}


def deepseek_once():
    """对照：我们现网出稿链路的模型侧耗时（单次非流式，等价 one_shot）。"""
    import urllib.request
    body = json.dumps({"model": DS_MODEL, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"【对方发言】\n{OPP}\n\n请按 ===SPEECH=== 标记输出可直接播报的反驳稿。"},
    ], "temperature": 0.4, "stream": False}).encode()
    req = urllib.request.Request(f"{DS_BASE}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {DS_KEY}",
                                          "Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=300).read())
    dt = time.time() - t0
    txt = (r["choices"][0]["message"].get("content") or "")
    u = r.get("usage") or {}
    return {"model": DS_MODEL, "total_s": round(dt, 2), "len": len(txt),
            "tokens": u.get("total_tokens"), "text": txt[:600]}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "quality"
    if mode == "quality":
        for i in range(3):
            r = asyncio.run(omni_once("qwen3.5-omni-plus-realtime"))
            if r.get("text"): r["quality"] = quality(r["text"])
            print(json.dumps({"run": i + 1, **{k: v for k, v in r.items() if k != "text"}}, ensure_ascii=False))
            print("   T:", r.get("text", "")[:400].replace("\n", " "))
        if DS_KEY:
            d = deepseek_once()
            d["quality"] = quality(d["text"])
            print(json.dumps({"CONTROL": {k: v for k, v in d.items() if k != "text"}}, ensure_ascii=False))
            print("   T:", d["text"][:400].replace("\n", " "))
    elif mode == "audio":
        r = asyncio.run(omni_once("qwen3.5-omni-plus-realtime", ("text", "audio"), voice="Tina"))
        print(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False))

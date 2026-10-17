"""评估 Omni Realtime 端到端模型的辩论出稿质量（文本输入，排除音频链路干扰）。
对照：同一提示词下 deepseek-flash 的出稿，供人工比对。
"""
import asyncio, json, os, sys, time

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

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

MODELS = ["qwen3.8-omni-flash-realtime", "qwen3.5-omni-plus-realtime",
          "qwen3.5-omni-flash-realtime"]


async def ask(model: str):
    import websockets
    hdr = {"Authorization": f"Bearer {KEY}", "OpenAI-Beta": "realtime=v1", "User-Agent": "Mozilla/5.0"}
    kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    ws = await websockets.connect(f"{BASE}?model={model}", **{kw: hdr}, max_size=None)
    await asyncio.wait_for(ws.recv(), timeout=10)  # session.created
    await ws.send(json.dumps({"type": "session.update", "session": {
        "modalities": ["text"], "voice": "Tina", "instructions": SYS,
        "turn_detection": None,   # 文本模式下关掉 VAD
    }}))
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ev.get("type") == "session.updated":
            break
        if ev.get("type") == "error":
            return {"model": model, "session_error": json.dumps(ev, ensure_ascii=False)[:300]}

    t0 = time.time()
    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": OPP}]}}))
    await ws.send(json.dumps({"type": "response.create",
                              "response": {"modalities": ["text"]}}))
    text, t_first, types = "", None, []
    while time.time() - t0 < 120:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        except asyncio.TimeoutError:
            break
        et = ev.get("type", "")
        types.append(et)
        if et.endswith("text.delta"):
            d = ev.get("delta") or ""
            if d and t_first is None: t_first = time.time() - t0
            text += d
        if et in ("response.done", "response.completed"):
            break
        if et == "error":
            return {"model": model, "error": json.dumps(ev, ensure_ascii=False)[:300], "types": types[:12]}
    try: await ws.close()
    except Exception: pass
    return {"model": model, "first_delta_s": round(t_first, 2) if t_first else None,
            "total_s": round(time.time() - t0, 2), "len": len(text),
            "text": text, "types": types[:8]}


def quality(text: str) -> dict:
    """按我们 ASSESSMENT 的硬要求打分（可量化观察点）。"""
    import re
    q = len(re.findall(r"[？?]", text))
    sents = [s for s in re.split(r"(?<=[。！？…])", text) if s.strip()]
    avg = round(sum(len(s) for s in sents) / len(sents), 1) if sents else 0
    peak = ["首先", "其次", "最后", "第一", "第二", "第三"]
    keys = ["请问", "那么", "如果", "是不是", "难道", "我们看", "反过来", "也就是说"]
    return {"设问句": q, "句数": len(sents), "平均句长": avg,
            "层次词命中": sum(1 for k in peak if k in text),
            "逻辑连接词命中": sum(1 for k in keys if k in text)}


if __name__ == "__main__":
    if not KEY: print("NO_KEY"); sys.exit(1)
    for m in MODELS:
        try:
            r = asyncio.run(ask(m))
        except Exception as e:
            print(json.dumps({"model": m, "fail": f"{type(e).__name__}: {e}"[:200]}, ensure_ascii=False)); continue
        if r.get("text"):
            r["quality"] = quality(r["text"])
        print(json.dumps(r, ensure_ascii=False))
        time.sleep(2)

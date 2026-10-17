"""出稿延迟实测：对比各模型的 SSE 首字节 / 首字 / 完成时间。

用法（在服务器容器内跑）：python3 /tmp/latency_probe.py
"""
import asyncio, json, time, statistics, sys

import httpx

BASE = "http://127.0.0.1:8000"
ROUNDS = 2
MODELS = sys.argv[1:] or ["deepseek-flash", "deepseek-chat", "glm-5.3-flash", "glm-5.3"]

PAYLOAD = {
    "topic": "人工智能的发展会消解还是拓展人的价值",
    "our_side": "正方（拓展）",
    "our_case": "我方认为人工智能拓展人的价值：它把重复劳动外包出去，让人从事创造、判断与关怀；历史上每次工具革命都扩展了人的能力边界，而非消解。",
    "opponent": (
        "对方辩友说人工智能拓展人的价值，但请看现实：长此以往，我们的写作、绘画、思考都开始依赖算法，"
        "侵蚀人的判断力和创造力。第二，人的能力会退化。"
        "第三，人工智能存在失控风险，一旦被滥用或被少数公司垄断，会加剧社会不平等，甚至威胁人类安全。"
        "所以人工智能发展的弊大于利。"
    ),
    "duration": 45,
}


async def one(model: str):
    t0 = time.perf_counter()
    first_byte = first_text = None
    chars = 0
    async with httpx.AsyncClient(timeout=180.0) as cli:
        async with cli.stream("POST", f"{BASE}/api/rebut", json={**PAYLOAD, "model": model}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if first_byte is None:
                    first_byte = time.perf_counter() - t0
                if line.startswith("data: "):
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    if ev.get("type") == "text":
                        if first_text is None:
                            first_text = time.perf_counter() - t0
                        chars += len(ev.get("delta") or "")
                    elif ev.get("type") == "done":
                        break
    return {
        "model": model,
        "ttfb": first_byte or 0,
        "first_text": first_text or 0,
        "total": time.perf_counter() - t0,
        "chars": chars,
    }


async def main():
    allres = {}
    for m in MODELS:
        rows = []
        for i in range(ROUNDS):
            try:
                r = await one(m)
                rows.append(r)
                print(f"  {m} #{i+1}  首字节 {r['ttfb']:.2f}s  首字 {r['first_text']:.2f}s  "
                      f"完成 {r['total']:.2f}s  {r['chars']}字", flush=True)
            except Exception as e:
                print(f"  {m} #{i+1}  FAILED: {type(e).__name__}: {e}", flush=True)
        if rows:
            allres[m] = rows

    print("\n=== 汇总 ===")
    print(f"{'模型':<18}{'首字(中位)':>12}{'完成(中位)':>12}{'字数':>8}")
    for m, rows in allres.items():
        ft = statistics.median([x["first_text"] for x in rows])
        tt = statistics.median([x["total"] for x in rows])
        cc = statistics.median([x["chars"] for x in rows])
        print(f"{m:<18}{ft:>10.2f}s{tt:>11.2f}s{cc:>8.0f}")


if __name__ == "__main__":
    asyncio.run(main())

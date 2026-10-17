"""出稿延迟实测 v2：拆解 SSE 各阶段耗时。

记录：TTFB / outline 阶段 / draft 首句 / 完成。
用法（服务器容器内）：python3 /tmp/latency_probe2.py [model ...]
"""
import asyncio, json, sys, time, statistics

import httpx

BASE = "http://127.0.0.1:8000"
ROUNDS = int(__import__("os").environ.get("ROUNDS", "2"))
MODELS = sys.argv[1:] or ["deepseek-flash", "glm-5.3-flash"]

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
    marks = {}
    chars = 0
    stages = []
    async with httpx.AsyncClient(timeout=180.0) as cli:
        async with cli.stream("POST", f"{BASE}/api/rebut", json={**PAYLOAD, "model": model}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if "ttfb" not in marks:
                    marks["ttfb"] = time.perf_counter() - t0
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("type")
                if t == "stage":
                    stages.append(f"{ev.get('stage')}.{ev.get('status')}@{time.perf_counter()-t0:.2f}")
                    if ev.get("stage") == "draft" and ev.get("status") == "start":
                        marks.setdefault("draft_start", time.perf_counter() - t0)
                elif t == "sentence":
                    if "first_sent" not in marks:
                        marks["first_sent"] = time.perf_counter() - t0
                    chars += len(ev.get("text") or "")
                elif t == "warn":
                    print(f"    ! warn: {str(ev.get('text'))[:80]}", flush=True)
                elif t == "done":
                    marks["done"] = time.perf_counter() - t0
                    break
                elif t == "error":
                    print(f"    ! error: {str(ev.get('message'))[:120]}", flush=True)
    marks["total"] = time.perf_counter() - t0
    marks["chars"] = chars
    marks["model"] = model
    marks["_stages"] = stages
    return marks


async def main():
    summary = {}
    for m in MODELS:
        rows = []
        for i in range(ROUNDS):
            try:
                r = await one(m)
                rows.append(r)
                print(f"  {m} #{i+1}  TTFB {r.get('ttfb',0):.2f}  "
                      f"draft起 {r.get('draft_start',0):.2f}  "
                      f"首句 {r.get('first_sent',0):.2f}  完成 {r.get('done',r['total']):.2f}  "
                      f"{r['chars']}字", flush=True)
                print(f"      阶段: {' | '.join(r['_stages'][:8])}", flush=True)
            except Exception as e:
                print(f"  {m} #{i+1}  FAILED: {type(e).__name__}: {e}", flush=True)
        if rows:
            summary[m] = rows

    print("\n=== 汇总（中位）===")
    print(f"{'模型':<18}{'首句':>9}{'完成':>9}{'字数':>7}")
    for m, rows in summary.items():
        fs = statistics.median([x.get("first_sent", x["total"]) for x in rows])
        dn = statistics.median([x.get("done", x["total"]) for x in rows])
        cc = statistics.median([x["chars"] for x in rows])
        print(f"{m:<18}{fs:>8.2f}s{dn:>8.2f}s{cc:>7.0f}")


if __name__ == "__main__":
    asyncio.run(main())

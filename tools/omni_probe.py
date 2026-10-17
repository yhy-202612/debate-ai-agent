"""Omni 端到端通道实测：首字 / 首音 / 完成。用法：python3 /tmp/omni_probe.py [model] [voice]"""
import asyncio, base64, json, os, sys, time

import httpx

BASE = "http://127.0.0.1:8000"
MODEL = sys.argv[1] if len(sys.argv) > 1 else ""
VOICE = sys.argv[2] if len(sys.argv) > 2 else ""

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
if MODEL:
    PAYLOAD["model"] = MODEL
if VOICE:
    PAYLOAD["voice"] = VOICE


async def main():
    t0 = time.perf_counter()
    t_ttfb = t_text = t_audio = t_done = None
    chars = audio_bytes = sents = 0
    async with httpx.AsyncClient(timeout=180.0) as cli:
        async with cli.stream("POST", f"{BASE}/api/rebut-omni", json=PAYLOAD) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if t_ttfb is None:
                    t_ttfb = time.perf_counter() - t0
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                tp = ev.get("type")
                if tp == "text":
                    if t_text is None:
                        t_text = time.perf_counter() - t0
                    chars += len(ev.get("delta") or "")
                elif tp == "audio":
                    if t_audio is None:
                        t_audio = time.perf_counter() - t0
                    audio_bytes += len(base64.b64decode(ev.get("delta") or ""))
                elif tp == "sentence":
                    sents += 1
                elif tp == "done":
                    t_done = time.perf_counter() - t0
                    break
                elif tp == "error":
                    print("  ! error:", str(ev.get("message"))[:200])
                    break
    print(f"  模型={MODEL or '(默认)'} 音色={VOICE or '(默认)'}")
    print(f"  TTFB      {t_ttfb or 0:.2f}s")
    print(f"  首字      {(t_text or 0):.2f}s")
    print(f"  ★首音     {(t_audio or 0):.2f}s")
    print(f"  完成      {(t_done or time.perf_counter()-t0):.2f}s   字幕 {chars} 字 / 音频 {audio_bytes/1024:.0f}KB")
    print(f"  音频时长约 {audio_bytes/2/24000:.1f}s（24kHz PCM16 单声道）")


if __name__ == "__main__":
    asyncio.run(main())

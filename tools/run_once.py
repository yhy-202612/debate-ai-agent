"""单次出稿调试：直接调用 Engine，打印每步耗时（不起 HTTP 服务）。

用法：python3 tools/run_once.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from debate.engine import Engine  # noqa: E402

OPPONENT = (
    "对方同学认为，人工智能会取代大量人类工作，导致大规模失业，因此人工智能的发展是消极的。"
    "据对方所说，许多岗位都会被机器替代，劳动者将无工可打。"
)
OUR_SIDE = "积极"
OUR_CASE = "人工智能提升生产效率、创造新岗位，人机协作能放大人的能力，整体利大于弊。"
TOPIC = "人工智能的发展对人的作用与意义是积极还是消极"


async def main():
    engine = Engine()
    print("routes:", engine.describe(), flush=True)
    t0 = time.monotonic()
    async for ev in engine.rebut_stream(opponent=OPPONENT, our_side=OUR_SIDE,
                                        our_case=OUR_CASE, topic=TOPIC, duration=45):
        dt = round(time.monotonic() - t0, 2)
        if ev["type"] == "stage" and ev["status"] == "done":
            print(f"[{dt:>6}s] stage {ev['stage']} {ev.get('ms')}ms", flush=True)
        elif ev["type"] == "sentence":
            print(f"[{dt:>6}s] sentence {ev['idx']:>2}: {ev['text']}", flush=True)
        elif ev["type"] == "done":
            m = ev["meta"]
            print(f"[{dt:>6}s] DONE mode={m.get('mode')} first={m['first_sentence_ms']}ms "
                  f"elapsed={ev['elapsed']} chars={m['chars']} parallel={m.get('parallel')} "
                  f"aux={m.get('aux_sentence_counts')} flags={ev['flags']}", flush=True)
        elif ev["type"] in ("flag", "warn", "error"):
            print(f"[{dt:>6}s] {ev['type']}: {ev.get('text') or ev.get('error')}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

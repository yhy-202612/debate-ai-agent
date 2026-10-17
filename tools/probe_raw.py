"""诊断：直连 strong 路由，打印原始流式输出（检查分隔符与格式）。

用法：python3 tools/probe_raw.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from debate import prompts  # noqa: E402
from debate.config import load_routes  # noqa: E402
from debate.models import ChatClient  # noqa: E402

OPPONENT = ("对方同学认为，人工智能会取代大量人类工作，导致大规模失业，"
            "因此人工智能的发展是消极的。")


async def main():
    routes = load_routes()
    client = ChatClient(routes["strong"], 4096)
    prompt = prompts.render(
        prompts.ONE_SHOT, duration=45, lo=135, hi=180,
        topic="人工智能的发展对人的作用与意义是积极还是消极",
        our_side="积极",
        our_case="人工智能提升生产效率、创造新岗位，人机协作放大人的能力。",
        opponent=OPPONENT)
    buf = ""
    async for piece in client.stream(prompts.SYSTEM, prompt, temperature=0.4):
        buf += piece
    print("=== length:", len(buf))
    print("=== has SPEECH marker:", "===SPEECH===" in buf)
    print("=== has ANALYSIS marker:", "===ANALYSIS===" in buf)
    print("=== head 200 ===")
    print(repr(buf[:200]))
    print("=== tail 900 ===")
    print(repr(buf[-900:]))


if __name__ == "__main__":
    asyncio.run(main())

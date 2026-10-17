"""模型响应速度基准：对比各供应商/参数组合的流式首字与总耗时。

用法：
  DS_KEY=... GLM_KEY=... KIMI_KEY=... python3 tools/bench_models.py
"""
import asyncio
import json
import os
import time

import httpx

SYS = "你是高中辩论赛我方二辩。"
PROMPT = "用不超过40字，写一句反驳“AI会导致大规模失业”的话，口语化。"

DS = "https://api.deepseek.com/v1/chat/completions"
WW = "https://wawapii.com/v1/chat/completions"


def cases():
    out = []
    ds = os.getenv("DS_KEY")
    glm = os.getenv("GLM_KEY")
    kimi = os.getenv("KIMI_KEY")
    if ds:
        out += [
            ("deepseek-flash", DS, ds, {"model": "deepseek-flash"}),
            ("deepseek-flash effort=low", DS, ds,
             {"model": "deepseek-flash", "reasoning_effort": "low"}),
            ("deepseek-v4-pro", DS, ds, {"model": "deepseek-v4-pro"}),
            ("deepseek-v4-pro effort=low", DS, ds,
             {"model": "deepseek-v4-pro", "reasoning_effort": "low"}),
        ]
    if glm:
        out += [
            ("glm-5.3-flash", WW, glm, {"model": "glm-5.3-flash"}),
            ("glm-5.3", WW, glm, {"model": "glm-5.3"}),
        ]
    if kimi:
        out += [
            ("wawapii kimi-k3", WW, kimi, {"model": "kimi-k3"}),
            ("wawapii glm-5.3-flash(k2)", WW, kimi, {"model": "glm-5.3-flash"}),
        ]
    return out


async def run(name, url, key, base):
    payload = {**base, "messages": [{"role": "system", "content": SYS},
                                    {"role": "user", "content": PROMPT}],
               "stream": True, "max_tokens": 400}
    t0 = time.monotonic()
    first = None
    text = ""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", url, headers={
                    "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    print(f"{name:30s} HTTP {resp.status_code} {body[:140]!r}")
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
                    if delta.get("content"):
                        if first is None:
                            first = time.monotonic() - t0
                        text += delta["content"]
        print(f"{name:30s} first={first and round(first, 2)}s "
              f"total={round(time.monotonic() - t0, 2)}s  «{text.strip()[:44]}»")
    except Exception as exc:  # noqa: BLE001
        print(f"{name:30s} ERR {type(exc).__name__} {str(exc)[:90]}")


async def main():
    await asyncio.gather(*[run(*c) for c in cases()])


if __name__ == "__main__":
    asyncio.run(main())

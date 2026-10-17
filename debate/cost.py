"""M3 成本核算：统计 token 用量；单价仅在显式配置时估算（不猜价、不编造）。

- token 用量是确定事实，始终上报；
- 费用仅在设置了 DEBATE_PRICE_IN / DEBATE_PRICE_OUT（元/百万 token）时给出，属估算。
"""
from __future__ import annotations

from .config import price_in, price_out


def estimate(prompt_tokens: int, completion_tokens: int) -> dict:
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    priced = price_in() is not None or price_out() is not None
    out = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "priced": priced,
    }
    if priced:
        cost = prompt_tokens / 1e6 * (price_in() or 0.0) + completion_tokens / 1e6 * (price_out() or 0.0)
        out["cost"] = round(cost, 6)
        out["currency"] = "CNY"
    return out


class UsageAcc:
    """累计「一次生成请求」内多路模型调用的 token 用量。"""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0

    def add(self, usage: dict | None) -> None:
        if not usage:
            return
        try:
            self.prompt += int(usage.get("prompt_tokens") or 0)
            self.completion += int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            return
        self.calls += 1

    def snapshot(self) -> dict:
        snap = estimate(self.prompt, self.completion)
        snap["calls"] = self.calls
        return snap

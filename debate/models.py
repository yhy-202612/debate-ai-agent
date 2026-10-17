"""OpenAI 兼容模型客户端（异步，支持流式与非流式，附带 token 用量回调）。"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable

import httpx

from .config import Route


class ModelError(RuntimeError):
    """模型调用或输出解析失败。"""


class ChatClient:
    def __init__(self, route: Route, max_tokens: int = 4096):
        self.route = route
        self.max_tokens = max_tokens

    # ---------- 内部 ----------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.route.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, system: str, user: str, temperature: float,
                 max_tokens: int | None, stream: bool) -> dict:
        payload = {
            "model": self.route.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.max_tokens,
            "stream": stream,
        }
        if not self.route.no_temperature:
            # Claude 系（经中转）不支持非默认 temperature，必须省略该字段
            payload["temperature"] = temperature
        if stream:
            # 让上游在流末尾附带 usage 统计（部分中转不支持也安全，忽略即可）
            payload["stream_options"] = {"include_usage": True}
        if self.route.effort:
            # DeepSeek V4.1 等推理模型支持：low / high / max
            payload["reasoning_effort"] = self.route.effort
        return payload

    # ---------- 非流式 ----------
    async def complete(self, system: str, user: str, *, temperature: float = 0.6,
                       max_tokens: int | None = None,
                       on_usage: Callable[[dict], None] | None = None) -> str:
        if not self.route.ready:
            raise ModelError("模型未就绪：缺少 base_url / api_key / model")
        payload = self._payload(system, user, temperature, max_tokens, False)
        try:
            async with httpx.AsyncClient(timeout=self.route.timeout) as client:
                resp = await client.post(self.route.chat_url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ModelError(f"模型请求失败：{type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise ModelError(f"模型请求失败：HTTP {resp.status_code} {resp.text[:200]}")
        try:
            obj = resp.json()
            choice = obj["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError("模型未返回有效 JSON") from exc
        if on_usage:
            on_usage(obj.get("usage"))
        if not isinstance(text, str) or not text.strip():
            if choice.get("finish_reason") == "length":
                raise ModelError("模型输出被截断，请增大 DEBATE_MAX_TOKENS 或重试")
            raise ModelError("模型返回空内容")
        return text

    # ---------- 流式 ----------
    async def stream(self, system: str, user: str, *, temperature: float = 0.6,
                     max_tokens: int | None = None,
                     on_reasoning: Callable[[str], None] | None = None,
                     on_usage: Callable[[dict], None] | None = None) -> AsyncIterator[str]:
        """逐段产出 content 增量；reasoning_content 通过回调丢弃。"""
        if not self.route.ready:
            raise ModelError("模型未就绪：缺少 base_url / api_key / model")
        payload = self._payload(system, user, temperature, max_tokens, True)
        try:
            async with httpx.AsyncClient(timeout=self.route.timeout) as client:
                async with client.stream("POST", self.route.chat_url,
                                         headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise ModelError(f"模型请求失败：HTTP {resp.status_code} {body[:200]!r}")
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if on_usage and obj.get("usage"):
                            on_usage(obj["usage"])
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning and on_reasoning:
                            on_reasoning(reasoning)
                        piece = delta.get("content")
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            raise ModelError(f"模型请求失败：{type(exc).__name__}") from exc

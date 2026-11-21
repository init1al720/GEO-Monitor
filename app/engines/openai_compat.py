"""OpenAI 兼容协议的通用实现。

DeepSeek、通义千问（DashScope 兼容模式）、豆包（方舟 v3）都暴露
POST /chat/completions，差别只在 base_url、model 和密钥，所以共用一份代码。

重试策略（与需求基线一致）：最多 3 次，间隔 30s、120s。
超时、5xx、429 才重试；401/403 直接判 BLOCKED，重试没有意义。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.engines.base import EngineAnswer, EngineConfig, ResponseStatus

logger = get_logger(__name__)

# 重试时要判定的状态码：限流与临时故障
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
# 鉴权 / 欠费 / 封禁，重试无意义
_BLOCKED_STATUS = {401, 402, 403}


class OpenAICompatClient:
    """一家 OpenAI 兼容引擎的客户端。"""

    def __init__(self, config: EngineConfig, timeout: float | None = None) -> None:
        settings = get_settings()
        self.name = config.name
        self.model = config.model
        self._config = config
        self._timeout = timeout or settings.engine_timeout_seconds
        self._max_attempts = max(1, settings.engine_max_attempts)
        self._retry_base = settings.engine_retry_base_seconds
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(self._timeout),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ask(self, question: str) -> EngineAnswer:
        if not self._config.api_key:
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.BLOCKED,
                error=f"未配置 {self.name} 的 API key",
            )

        last: EngineAnswer | None = None
        for attempt in range(1, self._max_attempts + 1):
            last = await self._attempt(question, attempt)
            if not last.retryable:
                return last
            if attempt < self._max_attempts:
                # 429 通常带 Retry-After，按服务端说的等；否则指数退避
                hinted = last.meta.get("retry_after")
                wait = (
                    float(hinted)
                    if isinstance(hinted, int | float) and hinted > 0
                    else self._retry_base * (4 ** (attempt - 1))
                )
                # 加抖动：多个采集进程同时撞上限流时，避免同一秒一起重试
                wait *= random.uniform(0.85, 1.15)
                logger.warning(
                    "引擎调用失败，%.0fs 后重试 | %s | 第 %s/%s 次 | %s",
                    wait,
                    self.name,
                    attempt,
                    self._max_attempts,
                    last.error,
                )
                await asyncio.sleep(wait)
        assert last is not None
        return last

    async def _attempt(self, question: str, attempt: int) -> EngineAnswer:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": question}],
            "temperature": 0.3,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.TIMEOUT,
                error=f"超时（{self._timeout}s）：{exc}",
                attempts=attempt,
                retryable=True,
            )
        except httpx.HTTPError as exc:
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.ERROR,
                error=f"网络错误：{exc}",
                attempts=attempt,
                retryable=True,
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        meta: dict[str, Any] = {"elapsed_ms": elapsed_ms}

        if resp.status_code in _BLOCKED_STATUS:
            # 鉴权失败 / 欠费 / 封禁：重试没有意义，直接判死
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.BLOCKED,
                error=f"HTTP {resp.status_code}：{_brief(resp)}",
                attempts=attempt,
                meta=meta,
            )
        if resp.status_code in _RETRYABLE_STATUS:
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                meta["retry_after"] = int(retry_after)
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.ERROR,
                error=f"HTTP {resp.status_code}：{_brief(resp)}",
                attempts=attempt,
                retryable=True,
                meta=meta,
            )
        if resp.status_code >= 400:
            # 400 参数错、404 模型不存在、413 超长：改配置才能解决，不重试
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.ERROR,
                error=f"HTTP {resp.status_code}：{_brief(resp)}",
                attempts=attempt,
                meta=meta,
            )

        try:
            data = resp.json()
        except ValueError:
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.ERROR,
                error="返回不是合法 JSON",
                attempts=attempt,
                meta={**meta, "body": resp.text[:500]},
            )

        return self._parse(data, attempt, elapsed_ms)

    def _parse(self, data: dict[str, Any], attempt: int, elapsed_ms: float) -> EngineAnswer:
        choices = data.get("choices") or []
        if not choices:
            return EngineAnswer(
                engine=self.name,
                model=self.model,
                status=ResponseStatus.ERROR,
                error="返回里没有 choices",
                raw=data,
                attempts=attempt,
                meta={"elapsed_ms": elapsed_ms},
            )

        choice = choices[0]
        message = choice.get("message") or {}
        text = (message.get("content") or "").strip()
        finish = choice.get("finish_reason")

        usage = data.get("usage") or {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        cost = self._cost(tokens_in, tokens_out)

        if finish == "content_filter":
            status = ResponseStatus.REFUSED
        elif not text:
            status = ResponseStatus.EMPTY
        else:
            status = ResponseStatus.SUCCESS

        return EngineAnswer(
            engine=self.name,
            model=data.get("model") or self.model,
            text=text,
            raw=data,
            status=status,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            attempts=attempt,
            meta={"elapsed_ms": elapsed_ms, "finish_reason": finish},
        )

    def _cost(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        if tokens_in is None and tokens_out is None:
            return None
        price_in = self._config.price_in_per_1k
        price_out = self._config.price_out_per_1k
        if not price_in and not price_out:
            return None
        return round((tokens_in or 0) / 1000 * price_in + (tokens_out or 0) / 1000 * price_out, 6)


def _brief(resp: httpx.Response) -> str:
    """错误响应可能很长，日志与库里只留前 300 字。"""
    try:
        return resp.text[:300].replace("\n", " ")
    except Exception:  # pragma: no cover - 极端情况下取不到 body
        return "<无响应体>"

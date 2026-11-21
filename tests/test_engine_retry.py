"""引擎调用的重试判定。

判错的代价很直接：把「模型名写错」当成临时故障，一条要白等 30s + 120s，
20 个问题就是两个多小时全耗在等重试上，而配置错误重试一万次也一样。
"""

from __future__ import annotations

import httpx
import pytest

from app.engines.base import EngineConfig, ResponseStatus
from app.engines.openai_compat import OpenAICompatClient

_OK_BODY = {
    "model": "fake-model",
    "choices": [{"message": {"content": "推荐智标枪"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


def _client(handler) -> OpenAICompatClient:  # type: ignore[no-untyped-def]
    cfg = EngineConfig(name="test", base_url="https://api.test/v1", model="m", api_key="k")
    client = OpenAICompatClient(cfg)
    # 重试不要真的睡，否则测试跑几分钟
    client._retry_base = 0  # noqa: SLF001
    client._max_attempts = 3  # noqa: SLF001
    client._client = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(handler),
        base_url="https://api.test/v1",
    )
    return client


async def test_bad_request_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "model not found"}})

    answer = await _client(handler).ask("q")

    assert len(calls) == 1, "400 是配置问题，重试没有意义"
    assert answer.status == ResponseStatus.ERROR
    assert answer.retryable is False


async def test_unauthorized_is_blocked_without_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "invalid key"})

    answer = await _client(handler).ask("q")

    assert len(calls) == 1
    assert answer.status == ResponseStatus.BLOCKED


async def test_rate_limit_is_retried_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=_OK_BODY)

    answer = await _client(handler).ask("q")

    assert len(calls) == 2
    assert answer.status == ResponseStatus.SUCCESS
    assert answer.attempts == 2


async def test_server_error_gives_up_after_max_attempts() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="upstream down")

    answer = await _client(handler).ask("q")

    assert len(calls) == 3
    assert answer.status == ResponseStatus.ERROR
    assert answer.retryable is True


async def test_success_parses_usage_and_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_OK_BODY)

    cfg = EngineConfig(
        name="test",
        base_url="https://api.test/v1",
        model="m",
        api_key="k",
        price_in_per_1k=0.001,
        price_out_per_1k=0.002,
    )
    client = OpenAICompatClient(cfg)
    client._client = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(handler), base_url="https://api.test/v1"
    )

    answer = await client.ask("q")

    assert answer.tokens_in == 10
    assert answer.tokens_out == 20
    # 10/1000*0.001 + 20/1000*0.002 = 0.00001 + 0.00004
    assert answer.cost_usd == pytest.approx(0.00005)

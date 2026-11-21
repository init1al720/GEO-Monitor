"""引擎协议：所有引擎实现必须产出同一个 EngineAnswer。

关键约定：调用失败不抛异常给上层，而是把失败写进 EngineAnswer.status。
采集是批量的，一条失败不该打断整轮，失败样本要进库、要能被统计到。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol


class ResponseStatus(enum.StrEnum):
    """回答状态。只有 SUCCESS 会进入指标分母。"""

    SUCCESS = "success"
    EMPTY = "empty"  # 调通了但回答是空的
    REFUSED = "refused"  # 模型明确拒答
    TIMEOUT = "timeout"
    ERROR = "error"
    BLOCKED = "blocked"  # 鉴权失败、欠费、被风控


@dataclass(frozen=True)
class EngineConfig:
    """一家引擎的接入参数。密钥从环境变量来，不写死在这里。"""

    name: str
    base_url: str
    model: str
    api_key: str
    # 计费（美元 / 千 token），用于估算单次采集成本；不清楚就留 0
    price_in_per_1k: float = 0.0
    price_out_per_1k: float = 0.0


@dataclass
class EngineAnswer:
    """一次问答的完整结果，字段与 responses 表对应。"""

    engine: str
    model: str | None = None
    text: str = ""
    raw: dict[str, Any] | None = None
    status: ResponseStatus = ResponseStatus.SUCCESS
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    attempts: int = 1
    # 这次失败值不值得重试。限流、5xx、超时要重试；参数写错、模型名不存在、
    # 鉴权失败重试一万次也一样，白等几分钟还占着并发位
    retryable: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class EngineError(Exception):
    """配置或协议层面的错误（缺密钥、返回结构不对），采集器会转成 BLOCKED / ERROR。"""


class EngineClient(Protocol):
    """所有引擎实现的统一入口。"""

    name: str
    model: str

    async def ask(self, question: str) -> EngineAnswer:
        """向引擎提一个问题，返回标准化结果。不抛异常，失败写进 status。"""
        ...

    async def aclose(self) -> None:
        """释放底层 HTTP 连接。"""
        ...

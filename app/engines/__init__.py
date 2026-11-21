"""引擎适配层：把各家大模型的调用差异关在这个目录里。

加一家新引擎 = 在 openai_compat 里加一份配置，或写一个新 client 文件，
collector / analyzer / metrics 一行都不用改。
"""

from __future__ import annotations

from app.engines.base import EngineAnswer, EngineClient, EngineConfig, EngineError
from app.engines.registry import available_engines, build_client

__all__ = [
    "EngineAnswer",
    "EngineClient",
    "EngineConfig",
    "EngineError",
    "available_engines",
    "build_client",
]

"""引擎注册表：配置在这里，实现在 openai_compat。

新增一家引擎的步骤：
1. 在 app/core/config.py 加 xxx_api_key / xxx_base_url / xxx_model；
2. 在下面的 _CONFIGS 里加一条（含计费单价，用于成本统计）；
3. 不用改 collector、analyzer、metrics 任何一行。
"""

from __future__ import annotations

import importlib

from app.core.config import get_settings
from app.engines.base import EngineClient, EngineConfig, EngineError
from app.engines.openai_compat import OpenAICompatClient

# 计费单价（美元 / 千 token），按各官网公开价估算，只用于成本统计，不影响业务
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek": (0.00027, 0.0011),
    "qwen": (0.0004, 0.0012),
    "doubao": (0.00011, 0.00028),
}


def _configs() -> dict[str, EngineConfig]:
    s = get_settings()
    raw = {
        "deepseek": (s.deepseek_base_url, s.deepseek_model, s.deepseek_api_key),
        "qwen": (s.qwen_base_url, s.qwen_model, s.qwen_api_key),
        "doubao": (s.doubao_base_url, s.doubao_model, s.doubao_api_key),
    }
    out: dict[str, EngineConfig] = {}
    for name, (base_url, model, key) in raw.items():
        price_in, price_out = _PRICING.get(name, (0.0, 0.0))
        out[name] = EngineConfig(
            name=name,
            base_url=base_url,
            model=model,
            api_key=key,
            price_in_per_1k=price_in,
            price_out_per_1k=price_out,
        )
    return out


def available_engines() -> list[str]:
    """已配置密钥、可以真实调用的引擎。"""
    return [name for name, cfg in _configs().items() if cfg.api_key]


def all_engines() -> list[str]:
    """所有支持的引擎名，含未配置密钥的。"""
    return list(_configs())


def build_client(name: str) -> EngineClient:
    """按名字造一个客户端。调用方负责 aclose()。"""
    configs = _configs()
    if name not in configs:
        raise EngineError(f"未知引擎：{name}，可选 {sorted(configs)}")
    cfg = configs[name]
    if not cfg.api_key:
        raise EngineError(f"引擎 {name} 未配置 API key")

    # 有专属实现就优先用（比如某家协议不兼容时），否则走 OpenAI 兼容实现
    try:
        module = importlib.import_module(f"app.engines.{name}")
    except ModuleNotFoundError:
        return OpenAICompatClient(cfg)
    factory = getattr(module, "build", None)
    if factory is None:
        return OpenAICompatClient(cfg)
    return factory(cfg)

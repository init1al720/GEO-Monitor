"""配置读取：全部通过环境变量 / .env 注入，代码里不出现硬编码密钥与地址。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["local", "dev", "staging", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用
    app_name: str = "backend-framework"
    app_version: str = "0.1.0"
    env: Env = "local"
    debug: bool = False

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8000
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "*"
    request_id_header: str = "X-Request-ID"

    # 日志
    log_level: str = "INFO"
    log_json: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def docs_enabled(self) -> bool:
        """生产环境默认关闭交互式文档，避免暴露内部接口结构。"""
        return not self.is_prod

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免每次请求都解析环境变量。"""
    return Settings()

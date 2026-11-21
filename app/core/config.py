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

    # ---------- 数据库（Supabase Postgres；本地可用 SQLite） ----------
    # 形如 postgresql+asyncpg://postgres:<密码>@db.xxx.supabase.co:5432/postgres
    database_url: str = "sqlite+aiosqlite:///./data/geo.db"
    db_echo: bool = False

    # ---------- 采集 ----------
    # 每次采集的并发上限，各家引擎的限流不同，先保守取 3
    collect_concurrency: int = 3
    # 单个回答的超时（秒）
    engine_timeout_seconds: float = 120.0
    # 单次调用最多尝试次数（1 次首发 + 2 次重试）
    engine_max_attempts: int = 3
    # 重试退避基数（秒），第 n 次重试等待 base * 4^(n-1)：30s、120s
    engine_retry_base_seconds: float = 30.0
    # 单次 run 的问题数上限，防止误操作一次打出几百个请求
    collect_max_prompts: int = 50

    # 引擎密钥，按需填写；未填写的引擎在采集时会被跳过
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = "doubao-pro-32k"

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

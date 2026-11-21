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
    # 报告口径的时区：daily_metrics.metric_date 按这个时区切天，
    # 落库的 called_at 始终是 UTC。服务国内客户用 Asia/Shanghai。
    report_timezone: str = "Asia/Shanghai"

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
    # Supabase 直连（IPv4 网络可直连，IPv6 only 环境会 ENETUNREACH）：
    #   postgresql+asyncpg://postgres:<密码>@db.xxx.supabase.co:5432/postgres
    # Session pooler（推荐，服务器环境用它）：
    #   postgresql+asyncpg://postgres.<ref>:<密码>@aws-0-<region>.pooler.supabase.com:5432/postgres
    database_url: str = "sqlite+aiosqlite:///./data/geo.db"
    db_echo: bool = False
    # SSL 模式。留空则自动判断：远程 Postgres 用 require，本地 SQLite/127.0.0.1 不加密。
    # 显式写进 database_url 的 ?sslmode= 会被自动翻译成 asyncpg 认识的参数。
    db_ssl: str = ""
    # 连接池。Session pooler 的连接数有上限（免费档通常 15），别设太大。
    db_pool_size: int = 3
    db_max_overflow: int = 2

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
    # 采集成功后自动重算当天指标。关掉的话看板数字要等手动重算才更新
    collect_auto_metrics: bool = True
    # 超过这个分钟数还停在 pending/running 的 run 视为僵尸（进程重启、机器重启），
    # 不再阻塞新的采集。要大于最坏情况耗时：问题数 × 重试次数 × 退避总和
    collect_stale_minutes: int = 180

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

    # ---------- Supabase 鉴权 ----------
    # 项目地址，形如 https://xxxx.supabase.co（结尾不带斜杠）
    supabase_url: str = ""
    # 老项目 / 自建：Dashboard → Settings → API → JWT Secret，用于校验 HS256 签名
    supabase_jwt_secret: str = ""
    # 新项目默认用非对称签名（ES256/RS256），公钥从 JWKS 端点自动拉取，不需要 secret
    # token 的 aud 声明，Supabase 固定是 authenticated
    supabase_jwt_audience: str = "authenticated"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def supabase_issuer(self) -> str:
        """JWT 的 iss 声明。地址没配时返回空串，跳过 iss 校验。"""
        url = self.supabase_url.rstrip("/")
        return f"{url}/auth/v1" if url else ""

    @property
    def auth_configured(self) -> bool:
        """能用 secret 校验 HS256，或用 url 拉 JWKS 校验非对称签名。"""
        return bool(self.supabase_jwt_secret or self.supabase_url)

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

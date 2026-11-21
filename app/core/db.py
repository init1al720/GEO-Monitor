"""数据库连接：异步 engine + session 工厂。

数据层是 Supabase Postgres，采集器用 service_role 或直连串写入，
不受 RLS 约束（RLS 只约束 anon / authenticated 这类浏览器端角色）。

连接串有两个容易踩的坑，都在 _normalize_url 里处理掉：

1. ``sslmode`` 是 libpq / psycopg 的参数，asyncpg 不认。SQLAlchemy 会把
   URL query 里的项原样塞进 asyncpg.connect()，asyncpg 又把不认识的键
   当作会话参数发给服务端，于是报
   ``unrecognized configuration parameter "sslmode"``。
   这里把它翻译成 asyncpg 的 ``ssl``。
2. 直接写 ``postgresql://`` 会走同步驱动，create_async_engine 直接报错，
   自动补成 ``postgresql+asyncpg://``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# libpq 的 sslmode → asyncpg 的 ssl 参数
# asyncpg 只接受 bool 或 SSLContext；None 表示"先试明文，失败再加密"
_SSLMODE_TO_ASYNCPG: dict[str, bool | None] = {
    "disable": False,
    "allow": None,
    "prefer": None,
    "require": True,
    "verify-ca": True,
    "verify-full": True,
}

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "host.docker.internal"}


def _normalize_url(raw: str) -> tuple[str, dict[str, object]]:
    """把连接串整理成 asyncpg 能吃的形式，返回 (url, connect_args)。

    SQLite 原样返回，不做任何加工。
    """
    if raw.startswith("sqlite"):
        return raw, {}

    url = make_url(raw)

    # 1. 补上异步驱动。用错驱动时的报错信息很长，不如在这里就地修正
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")

    # 2. sslmode 从 query 里摘出来，翻译成 ssl 参数
    query: dict[str, object] = {}
    for key, value in url.query.items():
        # 重复参数 make_url 会存成元组，取第一个即可
        query[key] = value[0] if isinstance(value, tuple) else value

    sslmode = query.pop("sslmode", None)
    connect_args: dict[str, object] = {}
    if sslmode is not None:
        mode = str(sslmode).lower()
        if mode not in _SSLMODE_TO_ASYNCPG:
            raise ValueError(f"不支持的 sslmode：{sslmode}（可选 {sorted(_SSLMODE_TO_ASYNCPG)}）")
        connect_args["ssl"] = _SSLMODE_TO_ASYNCPG[mode]

    url = url.set(query=query)
    return url.render_as_string(hide_password=False), connect_args


def _resolve_ssl(url: str, connect_args: dict[str, object]) -> None:
    """决定要不要 SSL。优先级：URL 里的 sslmode > 配置 db_ssl > 按 host 猜。"""
    if "ssl" in connect_args:
        return  # URL 里已经写明了，不再覆盖

    settings = get_settings()
    configured = settings.db_ssl.strip().lower()
    if configured:
        if configured not in _SSLMODE_TO_ASYNCPG:
            raise ValueError(f"db_ssl 取值不合法：{settings.db_ssl}")
        connect_args["ssl"] = _SSLMODE_TO_ASYNCPG[configured]
        return

    # Supabase 强制 TLS，远程库默认打开。asyncpg 的 True 会校验证书链，
    # Supabase 用的是公共 CA 签发的证书，能通过校验。
    host = (make_url(url).host or "").lower()
    connect_args["ssl"] = host not in _LOCAL_HOSTS


def get_engine() -> AsyncEngine:
    """进程内单例。engine 内部维护连接池，不要每次调用都新建。"""
    global _engine
    if _engine is None:
        settings = get_settings()
        url, connect_args = _normalize_url(settings.database_url)
        kwargs: dict[str, object] = {"echo": settings.db_echo}

        if url.startswith("sqlite"):
            # SQLite 的开发配置，生产走 Postgres 不会进这个分支
            connect_args["check_same_thread"] = False
            kwargs["connect_args"] = connect_args
            kwargs["pool_pre_ping"] = True
        else:
            _resolve_ssl(url, connect_args)
            # Session pooler 的连接数有配额，池子别开太大；pre_ping 防
            # 池里拿到已被服务端掐掉的连接
            kwargs.update(
                {
                    "connect_args": connect_args,
                    "pool_size": settings.db_pool_size,
                    "max_overflow": settings.db_max_overflow,
                    "pool_pre_ping": True,
                    "pool_recycle": 1800,
                }
            )

        engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            # SQLite 默认 PRAGMA foreign_keys=OFF，DDL 上写的 ondelete=CASCADE
            # 会被静默忽略：删掉 projects 一行后，runs/responses/mentions 全成孤儿。
            # 这条必须在每条连接上打开，不能只写进 DDL。
            _enable_sqlite_foreign_keys(engine)
        _engine = engine
    return _engine


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """给 SQLite 的每条新连接执行 PRAGMA foreign_keys=ON。"""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection, _connection_record):  # noqa: ANN001, ANN202
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """一次事务一个 session：正常退出提交，异常回滚并继续抛出。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """进程退出前调用，释放连接池。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None

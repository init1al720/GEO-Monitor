"""数据库连接：异步 engine + session 工厂。

数据层是 Supabase Postgres，采集器用 service_role 或直连串写入，
不受 RLS 约束（RLS 只约束 anon / authenticated 这类浏览器端角色）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """进程内单例。engine 内部维护连接池，不要每次调用都新建。"""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        kwargs: dict[str, object] = {"echo": settings.db_echo, "future": True}
        if url.startswith("sqlite"):
            # SQLite 的开发配置，生产走 Postgres 不会进这个分支
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update({"pool_size": 5, "max_overflow": 5, "pool_pre_ping": True})

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

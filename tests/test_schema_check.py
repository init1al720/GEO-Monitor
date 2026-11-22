"""schema 体检的纯逻辑部分。

真正的 9 项检查要在 Postgres 上才有意义（本地没 PG），
这里只钉住两件容易回归的事：SQLite 下必须跳过而不是误报失败、
汇总时跳过项不能算进「需要处理」。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.schema_check import Check, summarize, verify_schema


async def test_sqlite_skips_instead_of_failing() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            assert isinstance(session, AsyncSession)
            checks = await verify_schema(session)

        assert len(checks) == 1
        assert checks[0].skipped is True
        assert checks[0].ok is True

        passed, total, skipped = summarize(checks)
        assert (passed, total, skipped) == (0, 1, 1)
    finally:
        await engine.dispose()


def test_summarize_counts_failures() -> None:
    checks = [
        Check("视图 4 个", True),
        Check("视图 security_invoker", False, "v_metric_cards"),
        Check("数据库类型", True, "skipped", skipped=True),
    ]

    passed, total, skipped = summarize(checks)

    assert passed == 1
    assert total == 3
    assert skipped == 1
    # 跳过项不该被算成「通过」也不能算成「需要处理」
    assert passed + skipped == total - 1

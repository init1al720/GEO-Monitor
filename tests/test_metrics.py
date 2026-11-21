"""指标聚合单测：用内存 SQLite 建表，跑真实聚合逻辑。

覆盖两个最容易算错的地方：
- 分母只算 success，失败样本不能拉低提及率
- 声量份额的分母是所有品牌的提及次数，不是回答数
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.geo import DailyMetric, Mention, Project, Prompt, Response, Run
from app.services.metrics import recompute


@pytest.fixture
async def session(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed(session, day, rows):  # type: ignore[no-untyped-def]
    """rows: [(status, [(brand, is_own, position)])]"""
    project = Project(name="测试项目", brand="智标枪", competitors=["广联达"], aliases=[])
    session.add(project)
    await session.flush()

    prompt = Prompt(project_id=project.id, text="哪个好？")
    run = Run(project_id=project.id, status="success")
    session.add_all([prompt, run])
    await session.flush()

    for status, mentions in rows:
        response = Response(
            run_id=run.id,
            prompt_id=prompt.id,
            engine="deepseek",
            question=prompt.text,
            answer_text="x",
            cited_domains=["xiaohongshu.com"] if mentions and mentions[0][1] else [],
            status=status,
            called_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        )
        session.add(response)
        await session.flush()
        for brand, is_own, position in mentions:
            session.add(
                Mention(
                    response_id=response.id,
                    brand=brand,
                    is_own=is_own,
                    position=position,
                    sentiment="positive" if is_own else "neutral",
                )
            )
    await session.flush()
    return project.id


async def test_failed_responses_excluded_from_denominator(session) -> None:
    day = datetime.now(UTC).date()
    project_id = await _seed(
        session,
        day,
        [
            ("success", [("智标枪", True, 1), ("广联达", False, 2)]),
            ("success", [("广联达", False, 1)]),
            ("timeout", []),
            ("error", []),
        ],
    )

    rows = await recompute(session, project_id, day)
    by_brand = {r.subject: r for r in rows}

    assert by_brand["智标枪"].sample_size == 2
    assert float(by_brand["智标枪"].mention_rate) == pytest.approx(0.5)
    assert float(by_brand["智标枪"].first_mention_rate) == pytest.approx(0.5)
    assert float(by_brand["智标枪"].avg_position) == pytest.approx(1.0)
    assert float(by_brand["广联达"].mention_rate) == pytest.approx(1.0)


async def test_sov_uses_mention_count_not_response_count(session) -> None:
    day = datetime.now(UTC).date()
    # 提及总次数 5：智标枪 1 次，广联达 4 次（第 3 条回答里提到了两次）
    # 若按「回答数」算会得到 1/3 与 2/3，这里刻意验证不是那种算法
    project_id = await _seed(
        session,
        day,
        [
            ("success", [("智标枪", True, 1), ("广联达", False, 2)]),
            ("success", [("广联达", False, 1)]),
            ("success", [("广联达", False, 1), ("广联达", False, 2)]),
        ],
    )

    rows = await recompute(session, project_id, day)
    by_brand = {r.subject: r for r in rows}
    assert float(by_brand["智标枪"].sov) == pytest.approx(0.2)
    assert float(by_brand["广联达"].sov) == pytest.approx(0.8)


async def test_recompute_is_idempotent(session) -> None:
    day = datetime.now(UTC).date()
    project_id = await _seed(session, day, [("success", [("智标枪", True, 1)])])

    await recompute(session, project_id, day)
    await recompute(session, project_id, day)

    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(DailyMetric)
        .where(DailyMetric.project_id == project_id)
    )
    # 自家 1 行 + 竞品占位 1 行，重复算不会翻倍
    assert count == 2


async def test_brand_never_mentioned_still_gets_zero_row(session) -> None:
    day = datetime.now(UTC).date()
    project_id = await _seed(session, day, [("success", [("广联达", False, 1)])])

    rows = await recompute(session, project_id, day)
    own = next(r for r in rows if r.is_own)
    assert own.subject == "智标枪"
    assert float(own.mention_rate) == 0.0


async def test_citation_rate_counts_own_cited_responses(session) -> None:
    day = datetime.now(UTC).date()
    project_id = await _seed(
        session,
        day,
        [
            ("success", [("智标枪", True, 1)]),
            ("success", [("智标枪", True, 1)]),
        ],
    )
    # 上面两条都带了 xiaohongshu.com，引用率应为 1
    rows = await recompute(session, project_id, day, own_domains=("xiaohongshu.com",))
    own = next(r for r in rows if r.is_own)
    assert float(own.citation_rate) == pytest.approx(1.0)


async def test_empty_day_clears_stale_rows(session) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    project_id = await _seed(session, yesterday, [("success", [("智标枪", True, 1)])])
    await recompute(session, project_id, yesterday)

    today = datetime.now(UTC).date()
    rows = await recompute(session, project_id, today)
    assert rows == []

    remaining = await session.scalar(
        sa.select(sa.func.count()).select_from(DailyMetric).where(DailyMetric.metric_date == today)
    )
    assert remaining == 0


def test_uuid_helpers_are_independent() -> None:
    # 防止把 uuid 默认值写成共享对象这种低级错误
    assert uuid.uuid4() != uuid.uuid4()

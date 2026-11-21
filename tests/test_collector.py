"""采集链路的集成测试：不真的调引擎，但走完整流程。

故意不 mock 数据库和聚合逻辑，只把「引擎」换成一个固定回答的假客户端，
这样能验证：回答落库 → 提及解析 → 指标重算 这条链路是通的。
线上第一次真采集之前，这是唯一能证明"采完看板就有数"的测试。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.clock import business_today
from app.engines.base import EngineAnswer, ResponseStatus
from app.models.base import Base
from app.models.geo import DailyMetric, Mention, Project, Prompt, Response, Run
from app.services import collector

ANSWER = "综合来看智标枪更适合中小团队，广联达功能更全但价格高。"


class FakeClient:
    """固定回答的假引擎，记录 ask/aclose 次数。"""

    name = "deepseek"
    model = "fake-model"

    def __init__(self) -> None:
        self.asks = 0
        self.closed = 0

    async def ask(self, question: str) -> EngineAnswer:
        self.asks += 1
        return EngineAnswer(
            engine="deepseek",
            model=self.model,
            text=ANSWER,
            status=ResponseStatus.SUCCESS,
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.001,
        )

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture
async def factory(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/collect.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@asynccontextmanager
async def _scope(factory):  # type: ignore[no-untyped-def]
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _prepare(factory, monkeypatch) -> tuple[uuid.UUID, FakeClient]:  # type: ignore[no-untyped-def]
    fake = FakeClient()
    monkeypatch.setattr(collector, "session_scope", lambda: _scope(factory))
    monkeypatch.setattr(collector, "build_client", lambda name: fake)

    async with factory() as session:
        project = Project(
            name="采集测试",
            brand="智标枪",
            aliases=["智标枪"],
            competitors=["广联达"],
        )
        session.add(project)
        await session.flush()
        session.add_all(
            [
                Prompt(project_id=project.id, text="施工投标用什么软件？"),
                Prompt(project_id=project.id, text="哪个投标软件性价比高？"),
            ]
        )
        await session.commit()
        return project.id, fake


async def test_collect_writes_responses_mentions_and_metrics(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    project_id, fake = await _prepare(factory, monkeypatch)

    result = await collector.run_once(project_id, engines=["deepseek"])

    assert result.status == "success"
    assert result.success == 2
    assert result.failed == 0
    assert result.run_id is not None
    # 一个引擎只建一个客户端，两个问题复用同一个连接
    assert fake.asks == 2
    assert fake.closed == 1

    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(Response)) == 2
        # 两个品牌都被提到，每条回答各 2 条提及
        assert await session.scalar(sa.select(sa.func.count()).select_from(Mention)) == 4

        # 采集后自动算指标：自家 + 竞品各一行
        metrics = list((await session.scalars(sa.select(DailyMetric))).all())
        assert {m.subject for m in metrics} == {"智标枪", "广联达"}
        assert {m.metric_date for m in metrics} == {business_today()}
        own = next(m for m in metrics if m.is_own)
        assert own.sample_size == 2
        assert float(own.mention_rate) == 1.0


async def test_run_id_is_reused_not_duplicated(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """接口层先建 pending 记录再排后台任务，后台必须复用这条，不能新建。"""
    project_id, _ = await _prepare(factory, monkeypatch)

    async with factory() as session:
        run = Run(project_id=project_id, status="pending", prompt_count=2, engine_count=1)
        session.add(run)
        await session.commit()
        run_id = run.id

    result = await collector.run_once(
        project_id, run_id=run_id, engines=["deepseek"], max_prompts=1
    )

    assert result.run_id == run_id
    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(Run)) == 1
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.status == "success"
        assert run.finished_at is not None


async def test_dry_run_writes_nothing(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    project_id, fake = await _prepare(factory, monkeypatch)

    result = await collector.run_once(project_id, engines=["deepseek"], dry_run=True)

    assert result.status == "dry_run"
    assert result.success == 2
    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(Response)) == 0
        assert await session.scalar(sa.select(sa.func.count()).select_from(Run)) == 0


async def test_empty_prompt_bank_raises(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(collector, "session_scope", lambda: _scope(factory))
    async with factory() as session:
        project = Project(name="空项目", brand="X", aliases=[], competitors=[])
        session.add(project)
        await session.commit()
        pid = project.id

    with pytest.raises(ValueError, match="问题库"):
        await collector.run_once(pid, engines=["deepseek"])


async def test_all_engines_broken_is_reported(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """引擎客户端初始化失败时，整轮要能正常收尾，而不是抛异常留个 running。"""
    project_id, _ = await _prepare(factory, monkeypatch)

    def boom(name: str) -> FakeClient:
        raise RuntimeError("密钥格式不对")

    monkeypatch.setattr(collector, "build_client", boom)

    result = await collector.run_once(project_id, engines=["deepseek"])

    assert result.success == 0
    assert result.run_id is not None
    async with factory() as session:
        run = await session.get(Run, result.run_id)
        assert run is not None
        assert run.status == "failed"
        assert "密钥" in (run.error or "")

"""指标聚合：从 responses / mentions 算出 daily_metrics。

口径（与 docs/04 第 6 节、需求基线第 13 章一致）：

- 分母 sample_size = 当天 status='success' 的回答数。失败/超时/拒答不进分母，
  否则引擎一抖动，指标就掉，看不出是品牌的问题还是接口的问题。
- mention_rate      = 提到该品牌的回答数 / sample_size
- first_mention_rate= 该品牌位次为 1 的回答数 / sample_size
- avg_position      = 被提及时的位次均值（越小越好）
- sov               = 该品牌提及次数 / 所有品牌提及次数之和
- citation_rate     = 引用了自家域名的回答数 / sample_size（只对自家有意义）
- sentiment_score   = 正面提及数 / 该品牌提及数 × 100
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.geo import DailyMetric, Mention, Project, Response, Run
from app.services.analyzer import own_domain_hit

logger = get_logger(__name__)

CALC_VERSION = "v0.1"

# 自家域名默认值，采集时可覆盖；用于计算引用率
DEFAULT_OWN_DOMAINS = ("xiaohongshu.com",)


@dataclass
class BrandStats:
    brand: str
    is_own: bool
    mentioned: int = 0
    first_mentioned: int = 0
    positions: list[int] = field(default_factory=list)
    mentions: int = 0
    positive: int = 0


def _q4(value: float | None) -> Decimal | None:
    """比率统一 4 位小数，与 numeric(6,4) 对齐。"""
    if value is None:
        return None
    return Decimal(f"{value:.4f}")


def _q2(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{value:.2f}")


async def recompute(
    session: AsyncSession,
    project_id: uuid.UUID,
    metric_date: date,
    *,
    engine: str | None = None,
    own_domains: tuple[str, ...] = DEFAULT_OWN_DOMAINS,
    dry_run: bool = False,
) -> list[DailyMetric]:
    """重算某项目某天的指标。

    先删后插而不是 upsert：`engine` 为 null 时 Postgres 不认为两个 null 相等，
    unique 约束挡不住重复行，upsert 会插出两份，看板数字就翻倍了。
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在：{project_id}")

    stmt = (
        sa.select(Response)
        .join(Run, Run.id == Response.run_id)
        .where(
            Run.project_id == project_id,
            Response.status == "success",
            sa.func.date(Response.called_at) == metric_date,
        )
    )
    if engine is not None:
        stmt = stmt.where(Response.engine == engine)
    responses = list((await session.scalars(stmt)).all())

    if not responses:
        logger.warning("没有可用回答，跳过聚合 | 项目 %s | %s", project_id, metric_date)
        if not dry_run:
            await _delete_day(session, project_id, metric_date, engine)
        return []

    response_ids = [r.id for r in responses]
    mention_rows = list(
        (
            await session.scalars(sa.select(Mention).where(Mention.response_id.in_(response_ids)))
        ).all()
    )

    sample = len(responses)
    stats: dict[str, BrandStats] = {}

    def get_stats(brand: str, is_own: bool) -> BrandStats:
        if brand not in stats:
            stats[brand] = BrandStats(brand=brand, is_own=is_own)
        return stats[brand]

    # 自家与竞品先占位，一个都没提到时也要写 0，看板才不会缺卡片
    get_stats(project.brand, True)
    for name in project.competitors or []:
        if name and name != project.brand:
            get_stats(name, False)

    total_mentions = 0
    mentioned_responses: dict[str, set[uuid.UUID]] = defaultdict(set)
    first_responses: dict[str, set[uuid.UUID]] = defaultdict(set)

    for m in mention_rows:
        st = get_stats(m.brand, m.is_own)
        st.mentions += 1
        total_mentions += 1
        mentioned_responses[m.brand].add(m.response_id)
        if m.position == 1:
            first_responses[m.brand].add(m.response_id)
        if m.position is not None:
            st.positions.append(m.position)
        if m.sentiment == "positive":
            st.positive += 1

    for brand, ids in mentioned_responses.items():
        get_stats(brand, brand == project.brand).mentioned = len(ids)
    for brand, ids in first_responses.items():
        get_stats(brand, brand == project.brand).first_mentioned = len(ids)

    cited_own = sum(
        1 for r in responses if own_domain_hit(list(r.cited_domains or []), list(own_domains))
    )

    rows: list[DailyMetric] = []
    for brand, st in stats.items():
        mention_rate = st.mentioned / sample if sample else None
        first_rate = st.first_mentioned / sample if sample else None
        avg_pos = sum(st.positions) / len(st.positions) if st.positions else None
        sov = st.mentions / total_mentions if total_mentions else None
        citation = cited_own / sample if (st.is_own and sample) else None
        sentiment = st.positive / st.mentions * 100 if st.mentions else None

        rows.append(
            DailyMetric(
                project_id=project_id,
                metric_date=metric_date,
                engine=engine,
                subject=brand,
                is_own=st.is_own,
                sample_size=sample,
                mention_rate=_q4(mention_rate),
                first_mention_rate=_q4(first_rate),
                avg_position=_q2(avg_pos),
                sov=_q4(sov),
                citation_rate=_q4(citation),
                sentiment_score=_q2(sentiment),
                calc_version=CALC_VERSION,
            )
        )

    if dry_run:
        return rows

    await _delete_day(session, project_id, metric_date, engine)
    session.add_all(rows)
    await session.flush()

    logger.info(
        "指标重算完成 | 项目 %s | %s | 引擎 %s | 样本 %s | 品牌 %s",
        project_id,
        metric_date,
        engine or "全部",
        sample,
        len(rows),
    )
    return rows


async def _delete_day(
    session: AsyncSession, project_id: uuid.UUID, metric_date: date, engine: str | None
) -> None:
    cond = [
        DailyMetric.project_id == project_id,
        DailyMetric.metric_date == metric_date,
    ]
    if engine is None:
        cond.append(DailyMetric.engine.is_(None))
    else:
        cond.append(DailyMetric.engine == engine)
    await session.execute(sa.delete(DailyMetric).where(*cond))


async def recompute_range(
    session: AsyncSession,
    project_id: uuid.UUID,
    start: date,
    end: date,
) -> int:
    """补齐一段日期，用于首次上线或历史数据回算。返回写入行数。"""
    total = 0
    day = start
    while day <= end:
        rows = await recompute(session, project_id, day)
        total += len(rows)
        day = date.fromordinal(day.toordinal() + 1)
    return total

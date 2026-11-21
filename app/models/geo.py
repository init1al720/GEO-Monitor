"""GEO 平台六张业务表。

字段与 docs/sql/01-schema.sql 一一对应，改这里必须同步改那份 SQL，
否则采集器写入会报列不存在。表结构以 SQL 为准（线上由 SQL 建）。

说明：projects.created_by 由 02-auth.sql 追加，属于认证层的事，
这里不映射，避免 01 与 02 之间执行顺序造成插入失败。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonType


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


class Project(Base):
    """一个客户 / 一个品牌 = 一个 project。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    brand: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # ["小红书", "RED"]，判定提及时的全部候选词
    aliases: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    # ["抖音", "快手"]，雷达图与声量份额的对比对象
    competitors: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    @property
    def own_terms(self) -> list[str]:
        """自家品牌的全部命中词：品牌名 + 别名，去重保序。"""
        terms: list[str] = [self.brand, *list(self.aliases or [])]
        seen: set[str] = set()
        return [t for t in terms if t and not (t in seen or seen.add(t))]


class Prompt(Base):
    """监测问题库：向引擎提出的问题。"""

    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    category: Mapped[str | None] = mapped_column(sa.Text)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Run(Base):
    """一次采集任务：一个问题库 × 若干引擎 = 一个 run。"""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(sa.Text, default="pending", nullable=False)
    prompt_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    engine_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            "status in ('pending','running','success','partial','failed')",
            name="runs_status_check",
        ),
    )


class Response(Base):
    """单条回答：一个 run × 一个问题 × 一个引擎。

    raw_json 必须存原始返回，解析规则改动后可以离线重跑，不用重新花钱采集。
    """

    __tablename__ = "responses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str | None] = mapped_column(sa.Text)
    # 冗余存问题原文：问题库改写后，历史回答对应的还是当时那句问话
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer_text: Mapped[str | None] = mapped_column(sa.Text)
    raw_json: Mapped[dict | None] = mapped_column(JsonType)
    cited_domains: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, default="success", nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(sa.Integer)
    tokens_out: Mapped[int | None] = mapped_column(sa.Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 6))
    called_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            "status in ('success','empty','refused','timeout','error','blocked')",
            name="responses_status_check",
        ),
    )


class Mention(Base):
    """回答里识别出的品牌提及，一个回答可能提到多个品牌。"""

    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    response_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("responses.id", ondelete="CASCADE"), nullable=False
    )
    brand: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_own: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    # 第几个被提到，1 开始；null 表示提到了但没参与排位
    position: Mapped[int | None] = mapped_column(sa.Integer)
    sentiment: Mapped[str | None] = mapped_column(sa.Text)
    # 命中的别名，误判时靠它回溯
    matched_term: Mapped[str | None] = mapped_column(sa.Text)
    verified_by: Mapped[str] = mapped_column(sa.Text, default="rule", nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            "sentiment is null or sentiment in ('positive','neutral','negative')",
            name="mentions_sentiment_check",
        ),
        sa.CheckConstraint(
            "verified_by in ('rule','llm','human')",
            name="mentions_verified_by_check",
        ),
    )


class DailyMetric(Base):
    """每日聚合，看板的唯一数据源。

    engine 为 null 表示全引擎汇总（看板用的就是这一层），
    非 null 表示单引擎，留给后续按引擎拆分的图表。
    注意 unique 约束含 null 列，Postgres 不认为两个 null 相等，
    所以重算是「先删后插」，不靠 upsert。
    """

    __tablename__ = "daily_metrics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    engine: Mapped[str | None] = mapped_column(sa.Text)
    subject: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_own: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    sample_size: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    mention_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    first_mention_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    avg_position: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 2))
    sov: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    citation_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    sentiment_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    calc_version: Mapped[str] = mapped_column(sa.Text, default="v0.1", nullable=False)
    computed_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint(
            "project_id", "metric_date", "engine", "subject", name="daily_metrics_unique"
        ),
    )

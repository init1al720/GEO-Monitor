"""造演示数据。

目的：不接任何引擎、不花钱，也能让看板出真数字。
前端联调、汇报演示、验证视图都靠这份数据。

规则：所有假回答都写在 canned 里，能看出是示例文本，不会伪装成真实采集结果。
指标由真实的聚合逻辑算出（不是手填的百分比），所以视图、聚合、前后端联调
都能真实跑通。
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, time, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.core.logging import get_logger
from app.models.geo import DailyMetric, Mention, Project, Prompt, Response, Run
from app.services.analyzer import analyze, extract_domains
from app.services.metrics import recompute

logger = get_logger(__name__)

DEMO_BRAND = "智标枪"
DEMO_ALIASES = ["智标枪"]
DEMO_COMPETITORS = ["广联达", "鲁班软件", "新中大"]
DEMO_PROJECT_NAME = "演示项目 · 建筑投标软件"

DEMO_PROMPTS: list[tuple[str, str]] = [
    ("施工投标文件用什么软件制作比较好？", "工具选型"),
    ("投标文件排版容易出错，有什么解决方案？", "痛点"),
    ("建筑行业投标软件哪个性价比高？", "选购"),
    ("有没有支持多人协作的标书制作工具？", "协作"),
    ("投标文件电子签章和加密怎么处理？", "合规"),
]

# 回答模板：{own}/{c1}/{c2} 会被替换成品牌名，顺序决定位次
_ANSWER_TEMPLATES = [
    "目前主流的选择有几类。{c1} 在大型工程企业里用得比较多，功能全面但价格偏高；"
    "{own} 主打轻量化协作，排版校验做得比较细，口碑不错；{c2} 属于老牌产品，稳定性好。",
    "{own} 是比较值得推荐的一个，支持多人同时编辑标书，导出格式规范。"
    "如果预算充足也可以看 {c1}，功能更全但上手成本高。",
    "这类工具不少，{c1} 和 {c2} 都有成熟方案。{own} 的优势在于模板库丰富，"
    "适合中小团队快速出标。参考 https://www.zhihu.com/question/123456 上的讨论。",
    "据我了解，{c2} 在国企项目里更常见；{c1} 生态最完整；"
    "{own} 近两年增长很快，性价比高，缺点是部分高级功能要额外付费。",
    "建议先试用。{own} 提供免费额度，可以先跑一个项目；"
    "{c1} 支持私有化部署，数据合规要求高的团队更合适。",
    "坦白说没有绝对答案。{c1} 强在工程量清单联动，{own} 强在协作和版本管理，"
    "{c2} 强在盖章流程。看你们最痛的是哪一块。",
]

_SOURCES = [
    "参考资料：https://baike.baidu.com/item/投标 ",
    "参考 https://www.xiaohongshu.com/explore/abc ",
    "见 https://zhuanlan.zhihu.com/p/999999 ",
    "",
]


async def _purge_runs(session: AsyncSession, project_id: uuid.UUID) -> None:
    """清空一个项目下的采集结果与指标，保留问题库。重灌样本时用。"""
    run_ids = list(
        (await session.scalars(sa.select(Run.id).where(Run.project_id == project_id))).all()
    )
    if run_ids:
        await session.execute(
            sa.delete(Mention).where(
                Mention.response_id.in_(sa.select(Response.id).where(Response.run_id.in_(run_ids)))
            )
        )
        await session.execute(sa.delete(Response).where(Response.run_id.in_(run_ids)))
        await session.execute(sa.delete(Run).where(Run.project_id == project_id))
    await session.execute(sa.delete(DailyMetric).where(DailyMetric.project_id == project_id))
    await session.flush()


async def _purge_project(session: AsyncSession, project: Project) -> None:
    """删掉项目本身和它的全部子数据。

    不能只 ``session.delete(project)`` 指望数据库级联：SQLite 默认
    PRAGMA foreign_keys=OFF，SQLAlchemy 侧也没配 relationship，
    级联不会真的发生，子表会留下孤儿行。这里显式按依赖顺序删。

    目前只有 ``seed_demo(fresh=True)`` 走重置语义（复用 id，不删项目），
    这个函数留给后续"删除项目"功能用。
    """
    await _purge_runs(session, project.id)
    await session.execute(sa.delete(Prompt).where(Prompt.project_id == project.id))
    await session.delete(project)
    await session.flush()


async def seed_demo(
    *,
    days: int = 7,
    project_name: str = DEMO_PROJECT_NAME,
    with_samples: bool = True,
    fresh: bool = False,
    rng_seed: int = 20261121,
) -> uuid.UUID:
    """建一个演示项目（含问题库），可选灌入最近 N 天的回答并算好指标。

    返回项目 id。重复执行不会重复建项目；fresh=True 时清空原有数据重灌，
    项目 id 保持不变（前端书签里的 project_id 不会失效）。
    """
    rng = random.Random(rng_seed)

    async with session_scope() as session:
        existing = await session.scalar(sa.select(Project).where(Project.name == project_name))
        if existing is not None and not fresh:
            logger.info("演示项目已存在，跳过创建：%s（%s）", project_name, existing.id)
            project = existing
        elif existing is not None:
            # 复用原 id 重置：清采集数据 + 问题库，再按当前常量重建
            existing.brand = DEMO_BRAND
            existing.aliases = DEMO_ALIASES
            existing.competitors = DEMO_COMPETITORS
            await _purge_runs(session, existing.id)
            await session.execute(sa.delete(Prompt).where(Prompt.project_id == existing.id))
            await session.flush()
            project = existing
        else:
            project = Project(
                name=project_name,
                brand=DEMO_BRAND,
                aliases=DEMO_ALIASES,
                competitors=DEMO_COMPETITORS,
            )
            session.add(project)
            await session.flush()

        prompt_count = await session.scalar(
            sa.select(sa.func.count()).select_from(Prompt).where(Prompt.project_id == project.id)
        )
        if not prompt_count:
            session.add_all(
                [
                    Prompt(project_id=project.id, text=text, category=category)
                    for text, category in DEMO_PROMPTS
                ]
            )
            await session.flush()

        if not with_samples:
            logger.info("演示项目就绪（未灌样本）：%s", project.id)
            return project.id

        today = datetime.now(UTC).date()
        start = today - timedelta(days=days - 1)

        # 已有样本就整段重灌，避免同一份数据越叠越多
        await _purge_runs(session, project.id)

        prompts = list(
            (
                await session.scalars(
                    sa.select(Prompt)
                    .where(Prompt.project_id == project.id)
                    .order_by(Prompt.created_at)
                )
            ).all()
        )

        inserted = 0
        day = start
        while day <= today:
            for engine in ("deepseek", "qwen"):
                run = Run(
                    project_id=project.id,
                    status="success",
                    prompt_count=len(prompts),
                    engine_count=1,
                    started_at=datetime.combine(day, time(2, 0), tzinfo=UTC),
                    finished_at=datetime.combine(day, time(2, 5), tzinfo=UTC),
                )
                session.add(run)
                await session.flush()

                for prompt in prompts:
                    template = rng.choice(_ANSWER_TEMPLATES)
                    text = template.format(
                        own=DEMO_BRAND,
                        c1=DEMO_COMPETITORS[0],
                        c2=DEMO_COMPETITORS[1],
                    ) + rng.choice(_SOURCES)
                    response = Response(
                        run_id=run.id,
                        prompt_id=prompt.id,
                        engine=engine,
                        model=f"{engine}-demo",
                        question=prompt.text,
                        answer_text=text,
                        raw_json={"demo": True, "engine": engine},
                        cited_domains=extract_domains(text),
                        status="success",
                        tokens_in=rng.randint(60, 120),
                        tokens_out=rng.randint(200, 500),
                        cost_usd=None,
                        called_at=datetime.combine(day, time(2, 0), tzinfo=UTC),
                    )
                    session.add(response)
                    await session.flush()
                    detections = analyze(
                        text,
                        own_brand=project.brand,
                        own_terms=project.own_terms,
                        competitors=list(project.competitors or []),
                    )
                    session.add_all(
                        [
                            Mention(
                                response_id=response.id,
                                brand=d.brand,
                                is_own=d.is_own,
                                position=d.position,
                                sentiment=d.sentiment,
                                matched_term=d.matched_term,
                                verified_by="rule",
                            )
                            for d in detections
                        ]
                    )
                    inserted += 1
            day = date.fromordinal(day.toordinal() + 1)

        await session.flush()
        logger.info("灌入演示回答 %s 条 | 项目 %s", inserted, project.id)
        project_id = project.id

    # 聚合放在事务外重算，逐天提交，出错不会把整批数据回滚掉
    day = start
    total = 0
    while day <= today:
        async with session_scope() as session:
            rows = await recompute(session, project_id, day)
            total += len(rows)
        day = date.fromordinal(day.toordinal() + 1)

    logger.info("演示指标已生成 %s 行 | 项目 %s", total, project_id)
    return project_id

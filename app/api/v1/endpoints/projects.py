"""项目接口：触发采集、重算指标、看运行记录。

采集是分钟级的长任务，不能占着 HTTP 连接等它跑完（网关会 504），
所以 POST /collect 只负责校验权限、排进后台，立刻返回 202，
进度让前端轮询 GET /runs。

注意：BackgroundTasks 跑在当前进程里，进程重启会丢掉排队中的任务。
线上用 systemd timer 每天定时跑（docs/02-deploy.md），这个接口是给
"手动补跑一次"用的，不是调度器。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Query, status

from app.api.deps import DbSession, ProjectEditor, ProjectViewer
from app.core.clock import business_today
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.exceptions import BadRequestError, ConflictError
from app.core.logging import get_logger
from app.core.response import ApiResponse, success
from app.engines import available_engines
from app.models.geo import Prompt, Run
from app.schemas.project import (
    CollectAccepted,
    CollectIn,
    MetricsIn,
    MetricsOut,
    RunOut,
)
from app.services.collector import run_once
from app.services.metrics import recompute, recompute_range

logger = get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["项目"])

# 已经有任务在跑时不要再排一轮，否则同一批问题会重复计费
_ACTIVE_STATUSES = ("pending", "running")


@router.post(
    "/{project_id}/collect",
    response_model=ApiResponse[CollectAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    summary="触发一轮采集",
)
async def trigger_collect(
    project_id: uuid.UUID,
    payload: CollectIn,
    access: ProjectEditor,
    session: DbSession,
    background: BackgroundTasks,
):
    settings = get_settings()

    # 僵尸任务先收尸：进程重启会把 status 永久停在 running，
    # 不清理的话这个项目以后再也触发不了采集
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.collect_stale_minutes)
    stale = await session.execute(
        sa.update(Run)
        .where(
            Run.project_id == project_id,
            Run.status.in_(_ACTIVE_STATUSES),
            Run.created_at <= cutoff,
        )
        .values(
            status="failed",
            finished_at=datetime.now(UTC),
            error=f"超过 {settings.collect_stale_minutes} 分钟未完成，判定为中断（通常是进程重启）",
        )
    )
    if stale.rowcount:
        logger.warning("清理中断的采集任务 | 项目 %s | %s 条", project_id, stale.rowcount)

    running = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Run)
        .where(
            Run.project_id == project_id,
            Run.status.in_(_ACTIVE_STATUSES),
            Run.created_at > cutoff,
        )
    )
    if running:
        raise ConflictError("上一轮采集还在跑，等它结束再触发")

    engines = payload.engines or available_engines()
    if not engines:
        raise BadRequestError("没有任何引擎配置了 API 密钥，先填 DEEPSEEK_API_KEY 之类的环境变量")

    unknown = set(engines) - set(available_engines())
    if unknown:
        raise BadRequestError(f"这些引擎没有配置密钥：{', '.join(sorted(unknown))}")

    prompt_total = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Prompt)
        .where(Prompt.project_id == project_id, Prompt.enabled.is_(True))
    )
    if not prompt_total:
        raise BadRequestError("问题库是空的，先在 prompts 里加几条再采集")

    max_prompts = payload.max_prompts or settings.collect_max_prompts
    if max_prompts > settings.collect_max_prompts:
        raise BadRequestError(f"单轮问题数上限是 {settings.collect_max_prompts}")
    max_prompts = min(max_prompts, int(prompt_total))

    # 先把 run 落库再排后台任务。原来「先查有没有 running、后台才建 run」，
    # 两个并发请求会同时查不到而各排一轮，同一批问题被收两次费。
    run = Run(
        project_id=project_id,
        status="pending",
        prompt_count=max_prompts,
        engine_count=len(engines),
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    # 必须显式提交：后台任务在响应发出后立刻开始，比依赖清理（commit）更早，
    # 不提交的话它查不到这条记录
    await session.commit()

    background.add_task(_collect_in_background, project_id, run_id, engines, max_prompts)
    logger.info(
        "采集已排队 | 项目 %s | run %s | 引擎 %s | 上限 %s | 触发人 %s(%s)",
        project_id,
        run_id,
        ",".join(engines),
        max_prompts,
        access.user.email or access.user.id,
        access.role,
    )
    return success(
        CollectAccepted(
            project_id=project_id,
            run_id=run_id,
            engines=engines,
            max_prompts=max_prompts,
        )
    )


@router.post(
    "/{project_id}/metrics/recompute",
    response_model=ApiResponse[MetricsOut],
    summary="重算指标（不重新采集，不产生调用费用）",
)
async def recompute_metrics(
    project_id: uuid.UUID,
    payload: MetricsIn,
    access: ProjectEditor,
    session: DbSession,
):
    """规则调整后重算历史指标。只读已落库的回答，不调引擎，所以免费。"""
    if payload.metric_date is not None:
        rows = len(await recompute(session, project_id, payload.metric_date))
        days = 1
    else:
        end = business_today()
        start = end - timedelta(days=payload.days - 1)
        rows = await recompute_range(session, project_id, start, end)
        days = payload.days

    logger.info(
        "指标重算 | 项目 %s | %s 天 | %s 行 | 操作人 %s",
        project_id,
        days,
        rows,
        access.user.email or access.user.id,
    )
    return success(MetricsOut(project_id=project_id, days=days, rows=rows))


@router.get(
    "/{project_id}/runs",
    response_model=ApiResponse[list[RunOut]],
    summary="最近的采集记录",
)
async def list_runs(
    project_id: uuid.UUID,
    access: ProjectViewer,
    session: DbSession,
    limit: int = Query(default=20, ge=1, le=100, description="返回最近多少条"),
):
    rows = (
        await session.scalars(
            sa.select(Run)
            .where(Run.project_id == project_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    ).all()
    return success([RunOut.model_validate(row) for row in rows])


async def _collect_in_background(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    engines: list[str],
    max_prompts: int,
) -> None:
    """后台采集。异常必须在这里吃掉，否则会变成未处理的任务异常。

    失败时要把 run 标成 failed：留着 pending/running 会让这个项目
    在僵尸超时到点之前都触发不了新一轮。
    """
    try:
        result = await run_once(project_id, run_id=run_id, engines=engines, max_prompts=max_prompts)
        logger.info(
            "后台采集完成 | 项目 %s | run %s | 状态 %s | 成功 %s | 失败 %s | 成本 %.4f USD",
            project_id,
            run_id,
            result.status,
            result.success,
            result.failed,
            result.cost_usd,
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务没有调用方，只能记日志
        logger.exception("后台采集失败 | 项目 %s | run %s", project_id, run_id)
        await _mark_run_failed(run_id, exc)


async def _mark_run_failed(run_id: uuid.UUID, exc: BaseException) -> None:
    """把批次标成失败。单独开事务，因为主流程已经回滚了。"""
    try:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None or run.status not in _ACTIVE_STATUSES:
                return
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = f"{type(exc).__name__}: {exc}"[:1000]
    except Exception:  # noqa: BLE001
        logger.exception("标记采集失败也没成功 | run %s", run_id)

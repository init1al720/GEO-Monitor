"""采集器：把「问题 × 引擎」跑成一批回答，落库并当场做提及分析。

两个设计要点：

1. HTTP 并发、写库串行。同一进程里多个协程共用一个 AsyncSession 会报错
   （session 不是并发安全的），所以先把所有请求并发打完，再按顺序写。
   20 个问题 × 3 家引擎，串行发要十几分钟，并发抓完再写只要一两分钟。

2. 失败不中断。单条回答失败只记一条 status 非 success 的 responses，
   整轮的 run.status 记成 partial。这样「今天为什么指标掉了」能查到原因。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import sqlalchemy as sa

from app.core.clock import business_date
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.engines import EngineAnswer, EngineClient, build_client
from app.engines.base import ResponseStatus
from app.models.geo import Mention, Project, Prompt, Response, Run
from app.services.analyzer import analyze, extract_domains

logger = get_logger(__name__)


@dataclass
class RunResult:
    run_id: uuid.UUID | None
    prompt_count: int = 0
    engine_count: int = 0
    success: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    status: str = "pending"
    errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


async def run_once(
    project_id: uuid.UUID,
    *,
    run_id: uuid.UUID | None = None,
    engines: list[str] | None = None,
    max_prompts: int | None = None,
    dry_run: bool = False,
) -> RunResult:
    """跑一轮采集。dry_run=True 只调引擎、不写库，用来验证密钥和网络。

    传 run_id 时复用调用方已经落库的那条 pending 记录（接口层先建再排后台，
    这样并发的第二个请求能查到它，不会重复触发）。
    """
    settings = get_settings()

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"项目不存在：{project_id}")

        prompts = list(
            (
                await session.scalars(
                    sa.select(Prompt)
                    .where(Prompt.project_id == project_id, Prompt.enabled.is_(True))
                    .order_by(Prompt.created_at)
                )
            ).all()
        )
        if not prompts:
            raise ValueError(f"项目 {project.name} 没有问题库，先在 prompts 里加几条")
        limit = max_prompts or settings.collect_max_prompts
        prompts = prompts[:limit]

        engine_names = engines or _configured_engines()
        if not engine_names:
            raise ValueError("没有配置任何引擎密钥，先填 DEEPSEEK_API_KEY 之类的环境变量")

        result = RunResult(run_id=None, prompt_count=len(prompts), engine_count=len(engine_names))

        run: Run | None = None
        if run_id is not None and not dry_run:
            run = await session.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise ValueError(f"run 不存在或不属于该项目：{run_id}")
            run.prompt_count = len(prompts)
            run.engine_count = len(engine_names)
            run.status = "running"
            run.started_at = _now()
        elif not dry_run:
            run = Run(
                project_id=project_id,
                status="running",
                prompt_count=len(prompts),
                engine_count=len(engine_names),
                started_at=_now(),
            )
            session.add(run)

        if run is not None:
            await session.flush()
            result.run_id = run.id

        logger.info(
            "开始采集 | 项目 %s | run %s | %s 个问题 × %s 个引擎（%s）",
            project.name,
            result.run_id,
            len(prompts),
            len(engine_names),
            ",".join(engine_names),
        )

        # 先落一条 running，让前端和后端都能看到"已在跑"，
        # 也避免采集过程中进程崩了却什么记录都没留下
        if run is not None:
            await session.commit()

        answers, client_errors = await _gather_answers(
            prompts, engine_names, settings.collect_concurrency
        )
        result.errors.extend(client_errors)

        if dry_run:
            result.status = "dry_run"
            for prompt, engine, answer in answers:
                text = (answer.text or "").replace("\n", " ")[:60]
                logger.info(
                    "试跑 | %s | %s | %s | %s",
                    engine,
                    answer.status.value,
                    prompt.text[:20],
                    text,
                )
                if answer.status != ResponseStatus.SUCCESS:
                    result.failed += 1
                    result.errors.append(f"{engine}/{prompt.text[:12]}: {answer.error}")
                else:
                    result.success += 1
            return result

        assert run is not None
        affected_dates: set[date] = set()
        pending: list[Response] = []
        for prompt, engine, answer in answers:
            called_at = _now()
            # 按业务时区归日，不是 UTC：晚上跑完的采集该算今天
            affected_dates.add(business_date(called_at))
            response = Response(
                run_id=run.id,
                prompt_id=prompt.id,
                engine=engine,
                model=answer.model,
                question=prompt.text,
                answer_text=answer.text or None,
                raw_json=_shrink_raw(answer.raw),
                cited_domains=extract_domains(answer.text),
                status=answer.status.value,
                tokens_in=answer.tokens_in,
                tokens_out=answer.tokens_out,
                cost_usd=answer.cost_usd,
                called_at=called_at,
            )
            session.add(response)
            pending.append(response)

            if response.status == ResponseStatus.SUCCESS.value:
                result.success += 1
            else:
                result.failed += 1
                result.errors.append(f"{engine}/{prompt.text[:12]}: {answer.error}")
            if answer.cost_usd:
                result.cost_usd += float(answer.cost_usd)

        # 一次 flush 批量插入全部回答，再统一插入提及。
        # 原来每条 flush 一次，70 条回答要 70 个数据库往返；
        # 连线上 Postgres（Session pooler 走公网）时这部分能占掉大半写入耗时。
        await session.flush()
        for response in pending:
            if response.status == ResponseStatus.SUCCESS.value:
                session.add_all(_build_mentions(project, response))
        await session.flush()

        run.finished_at = _now()
        if result.success == 0:
            # 一条都没成功：全失败，或者引擎客户端一个都没建起来（answers 为空）。
            # 后一种情况 failed 也是 0，不能因为 failed==0 就标成功
            run.status = "failed"
            run.error = "; ".join(result.errors[:3])[:1000] or "没有任何回答产生"
        elif result.failed == 0:
            run.status = "success"
        else:
            run.status = "partial"
            run.error = "; ".join(result.errors[:3])[:1000]
        result.status = run.status

        logger.info(
            "采集结束 | run %s | 成功 %s / 失败 %s | 成本 $%.4f",
            run.id,
            result.success,
            result.failed,
            result.cost_usd,
        )

        # 回答先落库：指标算错不该把花了钱的回答一起回滚
        await session.commit()

        if settings.collect_auto_metrics and result.success:
            from app.services.metrics import recompute

            for day in sorted(affected_dates):
                try:
                    rows = await recompute(session, project_id, day)
                    logger.info("采集后自动重算 | %s | %s 行指标", day, len(rows))
                except Exception:  # noqa: BLE001 - 指标失败不影响本次采集结果
                    logger.exception("采集后自动重算失败 | 项目 %s | %s", project_id, day)
                    await session.rollback()

        return result


def _configured_engines() -> list[str]:
    from app.engines import available_engines

    return available_engines()


async def _gather_answers(
    prompts: list[Prompt], engine_names: list[str], concurrency: int
) -> tuple[list[tuple[Prompt, str, EngineAnswer]], list[str]]:
    """并发调用引擎，返回 (与输入同序的结果列表, 客户端初始化错误)。

    客户端按引擎各建一个复用：原来每个「问题 × 引擎」都新建一个 AsyncClient，
    60 个回答就是 60 次 TLS 握手，连接池完全没起作用，还容易撞上游的连接数限制。
    """
    clients: dict[str, EngineClient] = {}
    errors: list[str] = []
    for name in engine_names:
        try:
            clients[name] = build_client(name)
        except Exception as exc:  # noqa: BLE001 - 一家配错不该废掉整轮
            errors.append(f"{name}: 初始化失败 {exc}")
            logger.warning("引擎客户端初始化失败，跳过 | %s | %s", name, exc)

    usable = [name for name in engine_names if name in clients]
    if not usable:
        return [], errors

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(prompt: Prompt, engine_name: str) -> tuple[Prompt, str, EngineAnswer]:
        client = clients[engine_name]
        try:
            async with semaphore:
                return prompt, engine_name, await client.ask(prompt.text)
        except Exception as exc:  # 兜底：客户端本身炸了也要记一条
            return (
                prompt,
                engine_name,
                EngineAnswer(
                    engine=engine_name,
                    status=ResponseStatus.ERROR,
                    error=f"客户端异常：{exc}",
                ),
            )

    tasks = [one(p, e) for p in prompts for e in usable]
    try:
        answers = await asyncio.gather(*tasks)
    finally:
        for name, client in clients.items():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - 关连接失败不影响已有结果
                logger.warning("关闭引擎连接失败 | %s", name)
    return list(answers), errors


def _build_mentions(project: Project, response: Response) -> list[Mention]:
    detections = analyze(
        response.answer_text or "",
        own_brand=project.brand,
        own_terms=project.own_terms,
        competitors=list(project.competitors or []),
    )
    return [
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


def _shrink_raw(raw: dict | None) -> dict | None:
    """原样存返回，但砍掉超长字段，避免一条回答撑爆 jsonb。"""
    if not raw:
        return None
    trimmed = dict(raw)
    for choice in trimmed.get("choices") or []:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and len(content) > 20000:
            message["content"] = content[:20000]
    return trimmed


async def analyze_run(run_id: uuid.UUID) -> int:
    """重跑解析：解析规则改了以后不用重新花钱采集，直接对已存回答重算。

    先删该 run 下所有 mentions 再重建，保证幂等。
    """
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise ValueError(f"run 不存在：{run_id}")
        project = await session.get(Project, run.project_id)
        if project is None:
            raise ValueError(f"项目不存在：{run.project_id}")

        responses = list(
            (await session.scalars(sa.select(Response).where(Response.run_id == run_id))).all()
        )
        ids = [r.id for r in responses]
        if not ids:
            return 0

        await session.execute(sa.delete(Mention).where(Mention.response_id.in_(ids)))

        count = 0
        for response in responses:
            response.cited_domains = extract_domains(response.answer_text or "")
            if response.status != ResponseStatus.SUCCESS.value:
                continue
            mentions = _build_mentions(project, response)
            session.add_all(mentions)
            count += len(mentions)

        await session.flush()
        logger.info("重跑解析完成 | run %s | %s 条提及", run_id, count)
        return count

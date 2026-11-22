"""命令行入口。

    python -m app.cli check                     # 连通性 + 表结构自检
    python -m app.cli verify                    # 线上 schema 体检（视图/RLS/约束/列）
    python -m app.cli engines                   # 看哪些引擎配了密钥
    python -m app.cli init                      # 本地 SQLite 建表（线上用 SQL 脚本）
    python -m app.cli seed                      # 演示项目 + 7 天示例回答 + 指标
    python -m app.cli prompts --project <id>    # 看问题库 / 批量导入问题
    python -m app.cli collect --project <id>    # 真采集一轮（自动重算指标）
    python -m app.cli collect --all             # 所有项目各跑一轮（定时任务用）
    python -m app.cli metrics --project <id> --days 7
    python -m app.cli stats --project <id>      # 用量、成本、成功率
    python -m app.cli reanalyze --run <id>      # 规则改了，重跑解析不重新采集

生产上由 systemd timer 每天调用 `collect --all`，unit 文件在 deploy/ 下。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import case

from app.core.clock import business_today, day_window, report_tz
from app.core.db import dispose_engine, session_scope
from app.core.logging import get_logger, setup_logging
from app.models.geo import DailyMetric, Mention, Project, Prompt, Response, Run

logger = get_logger(__name__)

_EXPECTED_TABLES = ("projects", "prompts", "runs", "responses", "mentions", "daily_metrics")


async def cmd_init(_: argparse.Namespace) -> int:
    """本地建表（仅用于 SQLite 开发库）。

    线上是 Supabase Postgres，表由 docs/sql/01-schema.sql 建，
    这里建出来的表没有视图、没有 RLS，不能替代那份 SQL。
    """
    from app.core.db import get_engine
    from app.models.base import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("本地表已建好（不含视图与 RLS，线上请用 docs/sql/01-schema.sql）")
    return 0


async def cmd_check(_: argparse.Namespace) -> int:
    async with session_scope() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            print(f"数据库：{await session.scalar(sa.text('select version()'))}")
        else:
            print(f"数据库：{dialect}（本地开发库，视图与 RLS 不适用）")

        tables = await session.run_sync(
            lambda sync: sorted(sa.inspect(sync.connection()).get_table_names())
        )
        missing = [t for t in _EXPECTED_TABLES if t not in tables]
        if missing:
            print(f"缺少表：{', '.join(missing)}")
            print("→ 先在 Supabase SQL Editor 执行 docs/sql/01-schema.sql（和 02-auth.sql）")
            return 1

        print(f"表：{', '.join(_EXPECTED_TABLES)}")
        for model, label in (
            (Project, "项目"),
            (Prompt, "问题"),
            (Run, "采集批次"),
            (Response, "回答"),
            (Mention, "提及"),
            (DailyMetric, "指标行"),
        ):
            count = await session.scalar(sa.select(sa.func.count()).select_from(model))
            print(f"  {label:6s} {count}")
    return 0


async def cmd_verify(_: argparse.Namespace) -> int:
    """线上 schema 体检。只读系统表，可以随时在生产跑。"""
    from app.services.schema_check import summarize, verify_schema

    async with session_scope() as session:
        checks = await verify_schema(session)

    for check in checks:
        mark = "SKIP" if check.skipped else ("OK  " if check.ok else "FAIL")
        print(f"  [{mark}] {check.name:22s} {check.detail}")

    passed, total, skipped = summarize(checks)
    tail = f"（跳过 {skipped} 项）" if skipped else ""
    if passed + skipped == total:
        print(f"\n{passed}/{total} 项通过{tail}")
        return 0
    print(f"\n{passed}/{total} 项通过，{total - passed} 项需要处理{tail}")
    return 1


async def cmd_engines(_: argparse.Namespace) -> int:
    from app.engines import all_engines, available_engines

    ready = set(available_engines())
    for name in all_engines():
        print(f"  {name:10s} {'已配置' if name in ready else '缺 API key'}")
    if not ready:
        print("\n没有任何引擎可用。本地演示可以先跑：python -m app.cli seed --samples")
        return 1
    return 0


async def cmd_seed(args: argparse.Namespace) -> int:
    from app.services.seed import seed_demo

    project_id = await seed_demo(
        days=args.days,
        with_samples=not args.no_samples,
        fresh=args.fresh,
    )
    print(f"演示项目就绪：{project_id}")
    if not args.no_samples:
        print(f"已灌入 {args.days} 天示例回答并算好指标，前端登录后应能看到数字")
    return 0


def _parse_project(value: str) -> uuid.UUID | None:
    """允许传项目 id 或项目名，名字唯一时按名字找。"""
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def _resolve_project(value: str) -> uuid.UUID:
    direct = _parse_project(value)
    if direct is not None:
        return direct
    async with session_scope() as session:
        found = await session.scalar(sa.select(Project).where(Project.name == value))
        if found is None:
            raise SystemExit(f"找不到项目：{value}（可以传 id 或完整项目名）")
        return found.id


async def _collect_one(
    project_id: uuid.UUID,
    *,
    engines: list[str] | None,
    limit: int | None,
    dry_run: bool,
    recompute_metrics: bool,
) -> bool:
    """跑一个项目，返回是否算成功。异常不在这里吞，由调用方决定怎么记。"""
    from app.services.collector import run_once

    result = await run_once(
        project_id,
        engines=engines,
        max_prompts=limit,
        dry_run=dry_run,
    )
    print(
        f"  run {result.status} | 成功 {result.success} / 失败 {result.failed}"
        f" | 成本 ${result.cost_usd:.4f}"
    )
    for err in result.errors[:5]:
        print(f"    失败：{err}")
    if result.status == "dry_run":
        print("    （dry-run：没有写库，也没有生成指标）")
        return bool(result.success)

    if recompute_metrics and result.run_id:
        from app.services.metrics import recompute

        async with session_scope() as session:
            rows = await recompute(session, project_id, business_today())
        print(f"    已重算当天指标 {len(rows)} 行")
    return result.status in {"success", "partial"}


async def cmd_collect(args: argparse.Namespace) -> int:
    if bool(args.project) == bool(args.all):
        raise SystemExit("--project 和 --all 必须且只能给一个")

    engines = [e.strip() for e in args.engines.split(",") if e.strip()] or None

    if not args.all:
        project_id = await _resolve_project(args.project)
        ok = await _collect_one(
            project_id,
            engines=engines,
            limit=args.limit,
            dry_run=args.dry_run,
            recompute_metrics=args.metrics,
        )
        return 0 if ok else 1

    # 定时任务走这里：一个项目炸了不能拖住后面所有项目，
    # 所以逐个 try，最后统一用退出码告诉 systemd 这轮有没有失败。
    async with session_scope() as session:
        rows = (
            await session.execute(sa.select(Project.id, Project.name).order_by(Project.created_at))
        ).all()
    if not rows:
        print("没有任何项目，先建一个：python -m app.cli seed 或在前端新建")
        return 1

    failed: list[str] = []
    for project_id, name in rows:
        print(f"[{name}]")
        try:
            ok = await _collect_one(
                project_id,
                engines=engines,
                limit=args.limit,
                dry_run=args.dry_run,
                recompute_metrics=args.metrics,
            )
        except Exception as exc:  # noqa: BLE001 - 单个项目失败要继续跑下一个
            logger.warning("项目采集失败，继续下一个 | %s | %s", name, exc)
            print(f"    跳过：{exc}")
            ok = False
        if not ok:
            failed.append(name)

    total = len(rows)
    print(f"\n共 {total} 个项目，成功 {total - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败的项目：" + "、".join(failed))
        return 1
    return 0


async def cmd_metrics(args: argparse.Namespace) -> int:
    from app.services.metrics import recompute

    project_id = await _resolve_project(args.project)
    if args.date:
        days = [date.fromisoformat(args.date)]
    else:
        today = business_today()
        days = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    total = 0
    for day in days:
        async with session_scope() as session:
            rows = await recompute(session, project_id, day)
        total += len(rows)
        print(f"  {day} → {len(rows)} 行")
    print(f"共写入 {total} 行指标")
    return 0


async def cmd_reanalyze(args: argparse.Namespace) -> int:
    from app.services.collector import analyze_run

    count = await analyze_run(uuid.UUID(args.run))
    print(f"重跑解析完成，生成 {count} 条提及")
    return 0


def _read_prompt_lines(path_str: str) -> list[str]:
    """读问题库文本：一行一个问题，# 开头是注释，空行忽略。

    放在同步函数里读文件：CLI 是一次性进程，几毫秒的阻塞读不影响任何东西，
    异步函数里做文件 IO 反而会触发 lint 的 blocking-call 规则。
    """
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


async def cmd_prompts(args: argparse.Namespace) -> int:
    """查看问题库，或从文本文件批量导入。

    真实客户的问题库通常有 20~50 条，一条条用 SQL 插不现实。
    """
    project_id = await _resolve_project(args.project)

    texts: list[str] = []
    if args.add:
        texts.append(args.add.strip())
    if args.file:
        texts.extend(_read_prompt_lines(args.file))

    async with session_scope() as session:
        if texts:
            existing = set(
                (
                    await session.scalars(
                        sa.select(Prompt.text).where(Prompt.project_id == project_id)
                    )
                ).all()
            )
            added = skipped = 0
            for text in texts:
                if text in existing:
                    skipped += 1
                    continue
                session.add(
                    Prompt(project_id=project_id, text=text, category=args.category or None)
                )
                existing.add(text)
                added += 1
            print(f"新增 {added} 条，跳过重复 {skipped} 条")

        rows = list(
            (
                await session.scalars(
                    sa.select(Prompt)
                    .where(Prompt.project_id == project_id)
                    .order_by(Prompt.created_at)
                )
            ).all()
        )
        if not rows:
            print('问题库是空的。用 --file qs.txt 或 --add "问题" 导入')
            return 1

        print(f"共 {len(rows)} 条，启用 {sum(1 for r in rows if r.enabled)} 条：")
        for i, row in enumerate(rows, 1):
            mark = "✓" if row.enabled else "✗"
            print(f"  {i:2d}. {mark} [{row.category or '-':8s}] {row.text}")
    return 0


async def cmd_stats(args: argparse.Namespace) -> int:
    """用量与成本统计：每天回答数、成功率、token、花费。

    逐天用业务时区的时间区间查，而不是 group by date(called_at)：
    后者按 UTC 切天，国内客户看到的日报会错位 8 小时。
    """
    project_id = await _resolve_project(args.project)
    today = business_today()
    days = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    print(f"最近 {args.days} 天用量（项目 {project_id}，时区 {report_tz().key}）")
    print(f"  {'日期':12s}{'回答':>6s}{'成功':>6s}{'成功率':>8s}{'tokens':>10s}{'成本 USD':>10s}")

    total_cost = total_tokens = total_resp = total_ok = 0
    async with session_scope() as session:
        for day in days:
            start, end = day_window(day)
            row = (
                await session.execute(
                    sa.select(
                        sa.func.count().label("total"),
                        sa.func.sum(case((Response.status == "success", 1), else_=0)).label("ok"),
                        sa.func.sum(
                            sa.func.coalesce(Response.tokens_in, 0)
                            + sa.func.coalesce(Response.tokens_out, 0)
                        ).label("tokens"),
                        sa.func.sum(sa.func.coalesce(Response.cost_usd, 0)).label("cost"),
                    )
                    .join(Run, Run.id == Response.run_id)
                    .where(
                        Run.project_id == project_id,
                        Response.called_at >= start,
                        Response.called_at < end,
                    )
                )
            ).one()
            if not row.total:
                continue
            rate = f"{(row.ok or 0) / row.total:.0%}"
            print(
                f"  {str(day):12s}{row.total:>6d}{row.ok or 0:>6d}{rate:>8s}"
                f"{int(row.tokens or 0):>10d}{float(row.cost or 0):>10.4f}"
            )
            total_cost += float(row.cost or 0)
            total_tokens += int(row.tokens or 0)
            total_resp += int(row.total)
            total_ok += int(row.ok or 0)

        mentions = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Mention)
            .join(Response, Response.id == Mention.response_id)
            .join(Run, Run.id == Response.run_id)
            .where(Run.project_id == project_id, Mention.is_own.is_(True))
        )
        runs = (
            await session.execute(
                sa.select(Run.status, sa.func.count())
                .where(Run.project_id == project_id)
                .group_by(Run.status)
            )
        ).all()

    if not total_resp:
        print("\n这段时间没有采集记录")
        return 0

    rate = f"{total_ok / total_resp:.0%}" if total_resp else "-"
    print(
        f"  {'合计':12s}{total_resp:>6d}{total_ok:>6d}{rate:>8s}"
        f"{total_tokens:>10d}{total_cost:>10.4f}"
    )
    print(f"\n自家品牌提及：{mentions} 条")
    if runs:
        print("采集批次：" + "，".join(f"{status} {count}" for status, count in sorted(runs)))
    else:
        print("采集批次：无")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="GEO 采集与指标工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="本地 SQLite 建表（线上请用 SQL 脚本）")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="检查数据库连通性与表结构")
    p_check.set_defaults(func=cmd_check)

    p_verify = sub.add_parser("verify", help="线上 schema 体检：视图、RLS、唯一约束、视图列")
    p_verify.set_defaults(func=cmd_verify)

    p_engines = sub.add_parser("engines", help="查看各引擎密钥配置状态")
    p_engines.set_defaults(func=cmd_engines)

    p_seed = sub.add_parser("seed", help="创建演示项目并灌入示例数据")
    p_seed.add_argument("--days", type=int, default=7, help="生成最近几天（默认 7）")
    p_seed.add_argument("--no-samples", action="store_true", help="只建项目与问题库，不灌回答")
    p_seed.add_argument("--fresh", action="store_true", help="先删掉同名演示项目再重建")
    p_seed.set_defaults(func=cmd_seed)

    p_collect = sub.add_parser("collect", help="跑一轮真实采集")
    p_collect.add_argument("--project", default="", help="项目 id 或项目名")
    p_collect.add_argument("--all", action="store_true", help="所有项目各跑一轮（定时任务用）")
    p_collect.add_argument("--engines", default="", help="逗号分隔，默认所有已配置引擎")
    p_collect.add_argument("--limit", type=int, default=None, help="最多跑几个问题")
    p_collect.add_argument("--dry-run", action="store_true", help="只调引擎不写库")
    p_collect.add_argument(
        "--metrics", action="store_true", help="强制重算当天指标（COLLECT_AUTO_METRICS 已默认开）"
    )
    p_collect.set_defaults(func=cmd_collect)

    p_metrics = sub.add_parser("metrics", help="重算指标")
    p_metrics.add_argument("--project", required=True, help="项目 id 或项目名")
    p_metrics.add_argument("--date", default="", help="指定日期 YYYY-MM-DD")
    p_metrics.add_argument("--days", type=int, default=1, help="从今天往前算几天")
    p_metrics.set_defaults(func=cmd_metrics)

    p_prompts = sub.add_parser("prompts", help="查看或导入问题库")
    p_prompts.add_argument("--project", required=True, help="项目 id 或项目名")
    p_prompts.add_argument("--file", default="", help="每行一个问题的文本文件")
    p_prompts.add_argument("--add", default="", help="直接加一条问题")
    p_prompts.add_argument("--category", default="", help="导入时统一打的分组标签")
    p_prompts.set_defaults(func=cmd_prompts)

    p_stats = sub.add_parser("stats", help="最近 N 天的用量、成本与成功率")
    p_stats.add_argument("--project", required=True, help="项目 id 或项目名")
    p_stats.add_argument("--days", type=int, default=7, help="统计最近几天（默认 7）")
    p_stats.set_defaults(func=cmd_stats)

    p_rea = sub.add_parser("reanalyze", help="对已有 run 重跑提及解析")
    p_rea.add_argument("--run", required=True, help="run id")
    p_rea.set_defaults(func=cmd_reanalyze)

    return parser


async def _run(args: argparse.Namespace) -> int:
    try:
        return await args.func(args)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())

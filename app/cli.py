"""命令行入口。

    python -m app.cli check                     # 连通性 + 表结构自检
    python -m app.cli engines                   # 看哪些引擎配了密钥
    python -m app.cli init                      # 本地 SQLite 建表（线上用 SQL 脚本）
    python -m app.cli seed                      # 演示项目 + 7 天示例回答 + 指标
    python -m app.cli collect --project <id>    # 真采集一轮
    python -m app.cli metrics --project <id> --days 7
    python -m app.cli reanalyze --run <id>      # 规则改了，重跑解析不重新采集

生产上由 systemd timer 每天调用 collect + metrics，见 docs/02-deploy.md。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa

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


async def cmd_collect(args: argparse.Namespace) -> int:
    from app.services.collector import run_once

    project_id = await _resolve_project(args.project)
    engines = [e.strip() for e in args.engines.split(",") if e.strip()] or None
    result = await run_once(
        project_id,
        engines=engines,
        max_prompts=args.limit,
        dry_run=args.dry_run,
    )
    print(
        f"run {result.status} | 成功 {result.success} / 失败 {result.failed}"
        f" | 成本 ${result.cost_usd:.4f}"
    )
    for err in result.errors[:5]:
        print(f"  失败：{err}")
    if result.status == "dry_run":
        print("（dry-run：没有写库，也没有生成指标）")
        return 0 if result.success else 1
    if args.metrics and result.run_id:
        from app.services.metrics import recompute

        async with session_scope() as session:
            rows = await recompute(session, project_id, datetime.now(UTC).date())
        print(f"已重算当天指标 {len(rows)} 行")
    return 0 if result.status in {"success", "partial"} else 1


async def cmd_metrics(args: argparse.Namespace) -> int:
    from app.services.metrics import recompute

    project_id = await _resolve_project(args.project)
    if args.date:
        days = [date.fromisoformat(args.date)]
    else:
        today = datetime.now(UTC).date()
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="GEO 采集与指标工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="本地 SQLite 建表（线上请用 SQL 脚本）")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="检查数据库连通性与表结构")
    p_check.set_defaults(func=cmd_check)

    p_engines = sub.add_parser("engines", help="查看各引擎密钥配置状态")
    p_engines.set_defaults(func=cmd_engines)

    p_seed = sub.add_parser("seed", help="创建演示项目并灌入示例数据")
    p_seed.add_argument("--days", type=int, default=7, help="生成最近几天（默认 7）")
    p_seed.add_argument("--no-samples", action="store_true", help="只建项目与问题库，不灌回答")
    p_seed.add_argument("--fresh", action="store_true", help="先删掉同名演示项目再重建")
    p_seed.set_defaults(func=cmd_seed)

    p_collect = sub.add_parser("collect", help="跑一轮真实采集")
    p_collect.add_argument("--project", required=True, help="项目 id 或项目名")
    p_collect.add_argument("--engines", default="", help="逗号分隔，默认所有已配置引擎")
    p_collect.add_argument("--limit", type=int, default=None, help="最多跑几个问题")
    p_collect.add_argument("--dry-run", action="store_true", help="只调引擎不写库")
    p_collect.add_argument("--metrics", action="store_true", help="采集完立刻重算当天指标")
    p_collect.set_defaults(func=cmd_collect)

    p_metrics = sub.add_parser("metrics", help="重算指标")
    p_metrics.add_argument("--project", required=True, help="项目 id 或项目名")
    p_metrics.add_argument("--date", default="", help="指定日期 YYYY-MM-DD")
    p_metrics.add_argument("--days", type=int, default=1, help="从今天往前算几天")
    p_metrics.set_defaults(func=cmd_metrics)

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

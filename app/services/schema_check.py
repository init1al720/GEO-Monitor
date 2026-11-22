"""线上 schema 体检。

为什么不靠 `check`：`check` 只看表在不在，属于「服务能不能跑」的最低标准。
真正会让前端出错的是更细的东西——视图被 `create or replace` 之后
`security_invoker` 掉回 false、唯一约束没带 `nulls not distinct`、
某张表开了 RLS 但漏了策略。这些都查不到，只能等前端报 404 或数字漂移。

这里只读系统表，不写任何数据，可以随时跑，包括生产。
结论用「一项一行」输出，失败即退出码 1，能直接挂进 CI 或部署脚本。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# 01-schema.sql 建的 6 张业务表
BUSINESS_TABLES = ("projects", "prompts", "runs", "responses", "mentions", "daily_metrics")
# 02-auth.sql 建的 2 张认证表
AUTH_TABLES = ("profiles", "project_members")
# 前端直接读的 4 个视图
VIEWS = ("v_metric_cards", "v_source_distribution", "v_radar_metrics", "v_sample_summary")

# 视图的列是前端契约，少一列前端就白屏。列出必需列，多出来的不管。
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "v_metric_cards": ("project_id", "subject", "value_num", "value_text", "as_of"),
    "v_source_distribution": ("project_id", "name", "citation_count", "value"),
    "v_radar_metrics": ("project_id", "subject", "own_value", "competitor_value", "as_of"),
    "v_sample_summary": ("project_id", "samples", "as_of"),
}

# 这三张表只允许 service_role 写，authenticated 只能读
READ_ONLY_TABLES = ("responses", "mentions", "daily_metrics")

# 00-lock-write 的清理目标：这三张旧演示表上不该再有任何 "Allow public" 策略
LEGACY_TABLES = ("metrics", "source_distribution", "radar_metrics")


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


def _ok(name: str, detail: str = "") -> Check:
    return Check(name, True, detail)


def _bad(name: str, detail: str) -> Check:
    return Check(name, False, detail)


def _skip(name: str, detail: str) -> Check:
    return Check(name, True, detail, skipped=True)


async def verify_schema(session: AsyncSession) -> list[Check]:
    """返回全部检查项。调用方按 ok 汇总，不要在这里 raise。"""
    if session.get_bind().dialect.name != "postgresql":
        return [
            _skip(
                "数据库类型",
                "本地 SQLite，跳过（视图与 RLS 只存在于线上 Postgres）",
            )
        ]

    checks: list[Check] = []
    checks += await _check_tables(session)
    checks += await _check_views(session)
    checks += await _check_view_columns(session)
    checks += await _check_unique(session)
    checks += await _check_policies(session)
    checks += await _check_dup_metrics(session)
    return checks


async def _scalars(session: AsyncSession, stmt: sa.TextClause, **params: object) -> list[str]:
    result = await session.execute(stmt, params or None)
    return [str(row[0]) for row in result.all()]


async def _check_tables(session: AsyncSession) -> list[Check]:
    found = set(
        await _scalars(
            session,
            sa.text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' and table_type = 'BASE TABLE'"
            ),
        )
    )
    checks = []
    for group, names, hint in (
        ("业务表", BUSINESS_TABLES, "先执行 docs/sql/01-schema.sql"),
        ("认证表", AUTH_TABLES, "先执行 docs/sql/02-auth.sql"),
    ):
        missing = [n for n in names if n not in found]
        if missing:
            checks.append(_bad(f"{group} {len(names)} 张", f"缺 {', '.join(missing)} → {hint}"))
        else:
            checks.append(_ok(f"{group} {len(names)} 张", "齐全"))
    return checks


async def _check_views(session: AsyncSession) -> list[Check]:
    rows = (
        await session.execute(
            sa.text(
                "select c.relname, coalesce(array_to_string(c.reloptions, ','), '') "
                "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relkind = 'v'"
            )
        )
    ).all()
    opts = {str(name): str(o) for name, o in rows}

    missing = [v for v in VIEWS if v not in opts]
    checks = [
        _bad("视图 4 个", f"缺 {', '.join(missing)} → 执行 docs/sql/01-schema.sql")
        if missing
        else _ok("视图 4 个", "齐全")
    ]

    # security_invoker=false 时视图按 owner 权限跑，等于绕过 RLS，
    # 未登录也能读到别家项目的数据。replace view 会重置这个选项，容易漏。
    leaked = [v for v in VIEWS if v in opts and "security_invoker=true" not in opts[v]]
    checks.append(
        _bad("视图 security_invoker", f"{', '.join(leaked)} 会绕过 RLS，重新 set 一次")
        if leaked
        else _ok("视图 security_invoker", "4 个都已开启")
    )
    return checks


async def _check_view_columns(session: AsyncSession) -> list[Check]:
    stmt = sa.text(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public' and table_name in :names"
    ).bindparams(sa.bindparam("names", expanding=True))
    rows = (await session.execute(stmt, {"names": list(VIEWS)})).all()

    actual: dict[str, set[str]] = {}
    for table, column in rows:
        actual.setdefault(str(table), set()).add(str(column))

    problems = []
    for view, required in REQUIRED_COLUMNS.items():
        have = actual.get(view, set())
        if not have:
            continue  # 视图不存在已在上一项报过，不重复刷屏
        missing = [c for c in required if c not in have]
        if missing:
            problems.append(f"{view} 缺 {', '.join(missing)}")

    return [
        _bad("视图列（前端契约）", "；".join(problems))
        if problems
        else _ok("视图列（前端契约）", "必需列都在")
    ]


async def _check_unique(session: AsyncSession) -> list[Check]:
    defs = await _scalars(
        session,
        sa.text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.daily_metrics'::regclass and contype = 'u'"
        ),
    )
    joined = " | ".join(defs)

    if "engine" not in joined:
        return [_bad("daily_metrics 唯一约束", f"没找到覆盖 engine 的唯一约束：{joined or '无'}")]
    if "NULLS NOT DISTINCT" not in joined.upper():
        return [
            _bad(
                "daily_metrics 唯一约束",
                "缺 NULLS NOT DISTINCT，engine is null 的汇总行会重复 → 执行 docs/sql/03-fixes.sql",
            )
        ]
    return [_ok("daily_metrics 唯一约束", "覆盖 engine，且 treat null as equal")]


async def _check_policies(session: AsyncSession) -> list[Check]:
    rows = (
        await session.execute(
            sa.text(
                "select tablename, policyname, cmd, roles::text from pg_policies "
                "where schemaname = 'public'"
            )
        )
    ).all()

    # 谁可以写：只要 authenticated/anon/public 里有一条 INSERT/UPDATE/DELETE 策略就不算过
    write_cmds = {"INSERT", "UPDATE", "DELETE", "ALL"}
    writable = [
        f"{table}.{policy}({cmd})"
        for table, policy, cmd, _roles in rows
        if str(table) in READ_ONLY_TABLES and str(cmd).upper() in write_cmds
    ]

    legacy = [
        f"{table}.{policy}"
        for table, policy, _cmd, _roles in rows
        if str(table) in LEGACY_TABLES and str(policy).startswith("Allow public")
    ]

    return [
        _bad("只读表没有写策略", f"{', '.join(writable)}：这三张表应只由采集器（service_role）写")
        if writable
        else _ok("只读表没有写策略", "responses / mentions / daily_metrics 均只读"),
        _bad("旧表演示策略已清", f"残留 {', '.join(legacy)} → 执行 docs/sql/03-fixes.sql")
        if legacy
        else _ok("旧表演示策略已清", "无 Allow public 残留"),
    ]


async def _check_dup_metrics(session: AsyncSession) -> list[Check]:
    count = await session.scalar(
        sa.text(
            "select count(*) from ("
            "  select 1 from public.daily_metrics where engine is null"
            "  group by project_id, metric_date, subject having count(*) > 1"
            ") t"
        )
    )
    total = int(count or 0)
    return [
        _bad("汇总行无重复", f"有 {total} 组重复，看板数字会漂移 → 重跑 docs/sql/03-fixes.sql")
        if total
        else _ok("汇总行无重复", "每组 project/date/subject 只有一条")
    ]


def summarize(checks: Sequence[Check]) -> tuple[int, int, int]:
    """返回 (通过数, 总数, 跳过数)。"""
    return (
        sum(1 for c in checks if c.ok and not c.skipped),
        len(checks),
        sum(1 for c in checks if c.skipped),
    )

"""系统接口：存活探针与就绪探针，供 K8s / 负载均衡 / 监控调用。

/health 只证明进程活着，不碰任何外部依赖——数据库挂了它也该返回 200，
否则负载均衡会把还能提供静态接口的实例摘掉。
/ready 才真的查数据库和表结构，失败返回 503，让流量绕开这个实例。
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Response

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.core.response import success

logger = get_logger(__name__)

router = APIRouter(tags=["系统"])

# 就绪检查要求的表。少一张就说明 SQL 脚本没跑完，采集器写库必然报错
_REQUIRED_TABLES = ("projects", "prompts", "runs", "responses", "mentions", "daily_metrics")


@router.get("/health", summary="存活探针")
async def health() -> dict:
    settings = get_settings()
    return success(
        {
            "status": "ok",
            "name": settings.app_name,
            "version": settings.app_version,
            "env": settings.env,
        }
    )


@router.get("/ready", summary="就绪探针")
async def ready(response: Response) -> dict:
    """数据库、表结构、引擎与鉴权配置。任一硬依赖不可用就返回 503。"""
    settings = get_settings()
    checks: dict[str, str] = {}
    ready_flag = True

    try:
        async with session_scope() as session:
            await session.scalar(sa.text("select 1"))
            dialect = session.get_bind().dialect.name
            checks["database"] = dialect

            tables = set(
                await session.run_sync(lambda sync: sa.inspect(sync.connection()).get_table_names())
            )
            missing = [t for t in _REQUIRED_TABLES if t not in tables]
            if missing:
                checks["schema"] = f"缺少 {','.join(missing)}"
                ready_flag = False
            else:
                checks["schema"] = "ok"
    except Exception as exc:  # noqa: BLE001 - 探针不能因为异常直接 500
        logger.warning("就绪检查：数据库不可用 | %s", exc)
        checks["database"] = f"error: {type(exc).__name__}"
        ready_flag = False

    # 探针不做鉴权，只回状态，不回任何密钥内容
    from app.engines import available_engines

    engines = available_engines()
    checks["engines"] = ",".join(engines) if engines else "none"
    checks["auth"] = "configured" if settings.auth_configured else "not_configured"

    if not ready_flag:
        response.status_code = 503
    return success({"ready": ready_flag, "checks": checks})

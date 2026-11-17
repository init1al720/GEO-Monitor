"""系统接口：存活探针与就绪探针，供 K8s / 负载均衡调用。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.response import success

router = APIRouter(tags=["系统"])


@router.get("/health", summary="存活探针")
async def health() -> dict[str, Any]:
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
async def ready() -> dict[str, Any]:
    """接入数据库 / 缓存 / 第三方依赖后，把连通性检查加在这里。"""
    checks: dict[str, str] = {}
    return success({"ready": True, "checks": checks})

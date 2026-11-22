"""应用入口：组装中间件、异常处理、路由。

启动：
    uvicorn app.main:app --reload
    python -m app.main
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.middleware import RequestContextMiddleware
from app.core.response import success


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 启动钩子：建连接池、预热缓存、加载模型等
        logger.info(
            "应用启动 | name=%s env=%s version=%s",
            settings.app_name,
            settings.env,
            settings.app_version,
        )
        yield
        # 关闭钩子：释放连接池、刷写缓冲
        logger.info("应用关闭")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="后端基础框架：分层分区 + 统一响应 / 异常 / 日志",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Vercel 的预览域名每次部署都变，用正则匹配整段，避免把 * 打开
        allow_origin_regex=settings.cors_origin_regex or None,
        # 通配来源时不能开 credentials，否则浏览器会直接拒绝
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.request_id_header, "X-Response-Time-ms"],
    )
    # 后注册的中间件在最外层，请求 ID 必须最先拿到
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def index() -> dict:
        return success(
            {
                "name": settings.app_name,
                "version": settings.app_version,
                "env": settings.env,
                "docs": "/docs" if settings.docs_enabled else None,
                "api": settings.api_v1_prefix,
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=_settings.host,
        port=_settings.port,
        reload=_settings.debug,
    )

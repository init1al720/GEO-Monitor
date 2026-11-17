"""业务异常与全局异常处理。

用法：业务代码只 raise NotFoundError("用户不存在")，不要自己构造 HTTP 响应；
状态码与错误码在这里统一收口。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.response import failure

logger = logging.getLogger(__name__)


class AppError(Exception):
    """所有可预期业务异常的基类。"""

    code: str = "internal_error"
    status_code: int = 500
    default_message: str = "服务内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        data: Any = None,
    ) -> None:
        self.message = message or self.default_message
        self.data = data
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return failure(self.code, self.message, data=self.data)


class BadRequestError(AppError):
    code = "bad_request"
    status_code = 400
    default_message = "请求不合法"


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    default_message = "未登录或登录已过期"


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = 403
    default_message = "没有操作权限"


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    default_message = "资源不存在"


class ConflictError(AppError):
    code = "conflict"
    status_code = 409
    default_message = "资源冲突"


class TooManyRequestsError(AppError):
    code = "too_many_requests"
    status_code = 429
    default_message = "请求过于频繁"


class ServiceUnavailableError(AppError):
    code = "service_unavailable"
    status_code = 503
    default_message = "依赖服务不可用"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError) -> JSONResponse:
        log = logger.error if exc.status_code >= 500 else logger.warning
        log(
            "业务异常 | %s | %s",
            exc.code,
            exc.message,
            extra={"status_code": exc.status_code},
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("参数校验失败 | %s", exc.errors())
        return JSONResponse(
            status_code=422,
            content=failure(
                "validation_error",
                "请求参数校验失败",
                data=jsonable_encoder(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=failure(f"http_{exc.status_code}", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常 | %s", exc)
        message = f"{type(exc).__name__}: {exc}" if get_settings().debug else "服务内部错误"
        return JSONResponse(
            status_code=500,
            content=failure("internal_error", message),
        )

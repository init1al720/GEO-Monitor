"""HTTP 中间件：请求 ID、耗时统计、访问日志。"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.context import reset_request_id, set_request_id

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """给每个请求分配 request_id，并把耗时写回响应头与访问日志。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        header_name = settings.request_id_header

        request_id = request.headers.get(header_name) or uuid.uuid4().hex[:16]
        token = set_request_id(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "请求异常 | %s %s | %sms",
                request.method,
                request.url.path,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "client_ip": _client_ip(request),
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[header_name] = request_id
            response.headers["X-Response-Time-ms"] = str(duration_ms)
            logger.info(
                "请求完成 | %s %s | %s | %sms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "client_ip": _client_ip(request),
                },
            )
            return response
        finally:
            # 必须在日志之后重置，否则这条访问日志拿不到 request_id
            reset_request_id(token)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "-"

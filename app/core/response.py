"""统一响应体。

约定：所有 HTTP 接口都返回同一个信封结构，前端只需判断 code 一处。
    { "code": "ok", "message": "success", "data": ..., "request_id": "..." }

code 为 "ok" 表示成功，其余为业务错误码；HTTP 状态码另行表达传输层语义。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.context import get_request_id

T = TypeVar("T")

OK_CODE = "ok"


class ApiResponse(BaseModel, Generic[T]):
    code: str = Field(default=OK_CODE, description="业务码，ok 表示成功")
    message: str = Field(default="success", description="可直接展示给用户的提示")
    data: T | None = Field(default=None, description="业务数据")
    request_id: str | None = Field(default=None, description="链路追踪 ID，反馈问题时报这个")


def success(data: Any = None, *, message: str = "success") -> dict[str, Any]:
    return {
        "code": OK_CODE,
        "message": message,
        "data": data,
        "request_id": get_request_id(),
    }


def failure(code: str, message: str, *, data: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": get_request_id(),
    }

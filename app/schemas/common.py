"""通用出入参。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageQuery(BaseModel):
    """统一分页入参，直接作为 GET 的 Depends 使用。"""

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=200, description="每页条数")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size

    @classmethod
    def build(cls, query: PageQuery, total: int) -> PageMeta:
        return cls(page=query.page, page_size=query.page_size, total=total)


class PageData(BaseModel, Generic[T]):
    """分页数据体，放进 ApiResponse.data 使用。"""

    items: list[T]
    meta: PageMeta

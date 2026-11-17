"""示例接口：演示标准写法（统一响应体 + 依赖注入 + 业务异常抛给全局处理）。

这个文件是可以整块删掉的样板，删除后记得同步清理 v1/router.py 里的挂载。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import ExampleServiceDep, PageQueryDep
from app.core.response import ApiResponse, success
from app.schemas.common import PageData, PageMeta
from app.schemas.example import ItemCreate, ItemRead

router = APIRouter(prefix="/examples", tags=["示例"])


@router.get(
    "",
    response_model=ApiResponse[PageData[ItemRead]],
    summary="分页查询示例列表",
)
async def list_items(query: PageQueryDep, service: ExampleServiceDep):
    items, total = service.list_items(query)
    return success(PageData[ItemRead](items=items, meta=PageMeta.build(query, total)))


@router.post(
    "",
    response_model=ApiResponse[ItemRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建示例",
)
async def create_item(payload: ItemCreate, service: ExampleServiceDep):
    item = service.create_item(payload)
    return success(item, message="创建成功")


@router.get(
    "/{item_id}",
    response_model=ApiResponse[ItemRead],
    summary="查询单个示例",
)
async def get_item(item_id: str, service: ExampleServiceDep):
    return success(service.get_item(item_id))


@router.delete(
    "/{item_id}",
    response_model=ApiResponse[None],
    summary="删除示例",
)
async def delete_item(item_id: str, service: ExampleServiceDep):
    service.delete_item(item_id)
    return success(None, message="已删除")

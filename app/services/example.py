"""业务逻辑层：编排用例、做校验与事务，不关心 HTTP。

约定：endpoint 只做参数解析和调 service，service 不 import fastapi。
这里用内存字典代替仓储，接入数据库时把存储操作换成 repositories 调用即可。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from functools import lru_cache

from app.core.exceptions import NotFoundError
from app.schemas.common import PageQuery
from app.schemas.example import ItemCreate, ItemRead


class ExampleService:
    def __init__(self) -> None:
        self._items: dict[str, ItemRead] = {}

    def list_items(self, query: PageQuery) -> tuple[list[ItemRead], int]:
        items = sorted(self._items.values(), key=lambda item: item.created_at)
        total = len(items)
        window = items[query.offset : query.offset + query.page_size]
        return window, total

    def get_item(self, item_id: str) -> ItemRead:
        item = self._items.get(item_id)
        if item is None:
            raise NotFoundError(f"条目 {item_id} 不存在", data={"id": item_id})
        return item

    def create_item(self, payload: ItemCreate) -> ItemRead:
        item = ItemRead(
            id=uuid.uuid4().hex,
            name=payload.name,
            description=payload.description,
            created_at=datetime.now(UTC),
        )
        self._items[item.id] = item
        return item

    def delete_item(self, item_id: str) -> None:
        if self._items.pop(item_id, None) is None:
            raise NotFoundError(f"条目 {item_id} 不存在", data={"id": item_id})


@lru_cache
def get_example_service() -> ExampleService:
    return ExampleService()

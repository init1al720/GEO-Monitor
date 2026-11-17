"""仓储接口骨架：接入数据库时由具体实现类继承，对 service 暴露统一方法集。"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

ModelT = TypeVar("ModelT")
CreateT = TypeVar("CreateT")
UpdateT = TypeVar("UpdateT")


class BaseRepository(Generic[ModelT, CreateT, UpdateT]):
    """最小 CRUD 契约。

    真实实现里通常注入 AsyncSession：
        class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
            def __init__(self, session: AsyncSession) -> None:
                self.session = session
    """

    async def get(self, obj_id: str) -> ModelT | None:
        raise NotImplementedError

    async def list(self, *, offset: int = 0, limit: int = 20) -> list[ModelT]:
        raise NotImplementedError

    async def count(self) -> int:
        raise NotImplementedError

    async def create(self, payload: CreateT) -> ModelT:
        raise NotImplementedError

    async def update(self, obj_id: str, payload: UpdateT) -> ModelT | None:
        raise NotImplementedError

    async def delete(self, obj_id: str) -> bool:
        raise NotImplementedError

    async def bulk_create(self, payloads: list[CreateT]) -> list[ModelT]:
        raise NotImplementedError

    async def exists(self, **filters: Any) -> bool:
        raise NotImplementedError

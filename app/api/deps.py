"""依赖注入出口：endpoint 通过这里的别名拿 service / 当前用户 / 分页对象。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.schemas.common import PageQuery
from app.services.example import ExampleService, get_example_service

ExampleServiceDep = Annotated[ExampleService, Depends(get_example_service)]
PageQueryDep = Annotated[PageQuery, Depends()]

# 需要鉴权时在此处加：
# async def get_current_user(authorization: Annotated[str | None, Header()] = None) -> User:
#     ...  # 校验 token，失败 raise UnauthorizedError()
# CurrentUserDep = Annotated[User, Depends(get_current_user)]

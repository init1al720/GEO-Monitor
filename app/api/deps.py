"""依赖注入出口：endpoint 通过这里的别名拿数据库会话 / 当前用户 / 项目权限。

鉴权链路：
    Authorization: Bearer <supabase access_token>
        → verify_token()  校验签名与有效期，取出 user_id
        → require_project_role()  查 project_members，确认角色够不够

token 由前端直连 Supabase Auth 拿到，我们的服务器不签发也不刷新。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import AuthUser, verify_token
from app.schemas.common import PageQuery
from app.services.example import ExampleService, get_example_service
from app.services.membership import ALL_ROLES, CAN_MANAGE_PROJECT, CAN_WRITE_DATA, get_role

ExampleServiceDep = Annotated[ExampleService, Depends(get_example_service)]
PageQueryDep = Annotated[PageQuery, Depends()]


async def get_db() -> AsyncIterator[AsyncSession]:
    """一次请求一个事务：正常返回提交，抛异常回滚。"""
    async with session_scope() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


_bearer = HTTPBearer(
    auto_error=False,
    description="Supabase 登录后拿到的 access_token，放在 Authorization: Bearer 里",
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("缺少登录凭证，请在请求头带上 Authorization: Bearer <token>")
    return await verify_token(credentials.credentials)


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


@dataclass(frozen=True)
class ProjectAccess:
    """通过权限校验的上下文，端点里直接用，不用再查一次角色。"""

    project_id: uuid.UUID
    role: str
    user: AuthUser


def _user_uuid(user: AuthUser) -> uuid.UUID:
    try:
        return uuid.UUID(user.id)
    except ValueError as exc:
        raise UnauthorizedError("token 里的用户标识不是合法 UUID") from exc


def require_project_role(*allowed: str) -> Callable[..., Awaitable[ProjectAccess]]:
    """生成一个"必须是项目成员且角色在 allowed 里"的依赖。

    用法：
        MemberAccess = Annotated[ProjectAccess, Depends(require_project_role(*CAN_WRITE_DATA))]

    非成员一律按 404 处理，不返回 403——403 等于告诉对方"这个项目存在，
    只是你没权限"，项目 id 可枚举时会泄露客户名单。
    """

    async def dependency(
        project_id: uuid.UUID,
        user: CurrentUser,
        session: DbSession,
    ) -> ProjectAccess:
        role = await get_role(session, project_id, _user_uuid(user))
        if role is None:
            raise NotFoundError("项目不存在，或你不是该项目的成员")
        if role not in allowed:
            raise ForbiddenError(f"当前角色 {role} 不能执行该操作，需要 {'/'.join(allowed)}")
        return ProjectAccess(project_id=project_id, role=role, user=user)

    return dependency


# 端点的三种权限档位，直接拿去当类型标注
ProjectViewer = Annotated[ProjectAccess, Depends(require_project_role(*ALL_ROLES))]
ProjectEditor = Annotated[ProjectAccess, Depends(require_project_role(*CAN_WRITE_DATA))]
ProjectAdmin = Annotated[ProjectAccess, Depends(require_project_role(*CAN_MANAGE_PROJECT))]

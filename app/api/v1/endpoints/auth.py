"""认证接口。

这里**没有注册/登录**：那些请求直连 Supabase Auth，不经过我们的服务器。
这个模块只做一件事——把 token 换成"你是谁、你在哪些项目里是什么角色"，
前端拿它决定页面元素显不显示。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.response import ApiResponse, success
from app.models.auth import Profile
from app.schemas.auth import MeOut, ProjectRoleOut
from app.services.membership import list_memberships

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get(
    "/me",
    response_model=ApiResponse[MeOut],
    summary="当前登录用户与所属项目角色",
)
async def read_me(user: CurrentUser, session: DbSession):
    """前端登录后调一次，确认 token 能打通、拿到自己的角色。

    profile 可能还没建（触发器没执行、或刚注册的账号同步延迟），
    这时不报错，用 token 里的邮箱兜底展示。
    """
    user_id = _as_uuid(user.id)
    profile = await session.get(Profile, user_id) if user_id else None

    projects: list[ProjectRoleOut] = []
    if user_id is not None:
        projects = [
            ProjectRoleOut(
                id=project.id,
                name=project.name,
                brand=project.brand,
                role=role,
            )
            for project, role in await list_memberships(session, user_id)
        ]

    return success(
        MeOut(
            id=user.id,
            email=user.email,
            display_name=(profile.display_name if profile else None) or user.display_name,
            avatar_url=profile.avatar_url if profile else None,
            is_service=user.is_service,
            projects=projects,
        )
    )


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None

"""项目角色查询与判定。

权限判断只在这里做，接口层不要再自己拼 SQL 查 project_members，
否则同一套规则散在十几个地方，早晚有一处漏掉。

角色与权限矩阵见 docs/05-auth-permission.md：
    owner  > admin > member > viewer
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import ProjectMember
from app.models.geo import Project

# 数值越大权限越高，用于"至少要是某个角色"的判断
ROLE_ORDER = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}

ALL_ROLES = ("owner", "admin", "member", "viewer")
# 触发采集、改问题库需要 member 及以上
CAN_WRITE_DATA = ("owner", "admin", "member")
# 管项目信息、加删成员需要 admin 及以上
CAN_MANAGE_PROJECT = ("owner", "admin")


def role_rank(role: str | None) -> int:
    return ROLE_ORDER.get(role or "", 0)


def has_role(role: str | None, allowed: tuple[str, ...]) -> bool:
    return role in allowed


async def get_role(
    session: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str | None:
    """返回该用户在该项目里的角色，不是成员则返回 None。"""
    return await session.scalar(
        sa.select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )


async def list_memberships(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[tuple[Project, str]]:
    """当前用户能看到的全部项目及角色，按创建时间倒序。"""
    rows = await session.execute(
        sa.select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id)
        .order_by(Project.created_at.desc())
    )
    return [(project, role) for project, role in rows.all()]

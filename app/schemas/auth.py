"""认证相关出参。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ProjectRoleOut(BaseModel):
    """当前用户在一个项目里的身份。前端用它决定按钮显不显示。"""

    id: uuid.UUID
    name: str
    brand: str
    role: str = Field(description="owner / admin / member / viewer")


class MeOut(BaseModel):
    id: str = Field(description="Supabase auth.users.id")
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_service: bool = Field(default=False, description="是否为后端自己的 service_role 密钥")
    projects: list[ProjectRoleOut] = Field(default_factory=list)

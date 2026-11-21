"""认证层的两张表：profiles、project_members。

字段与 docs/sql/02-auth.sql 一一对应。线上这两张表由那份 SQL 建，
这里的模型主要用于：本地 SQLite 跑测试、按角色查权限、读用户资料。

**故意不映射两个外键**：
- `profiles.id → auth.users(id)`：auth schema 归 Supabase 管，本地 SQLite 里
  根本没有这张表，映射了连 create_all 都过不去；
- `project_members.user_id → profiles(id)`：数据由 02-auth.sql 的触发器保证，
  不在 ORM 层重复声明，避免本地建表顺序问题。

线的完整性靠数据库，ORM 只负责读。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


class Profile(Base):
    """用户业务资料。账号本身（邮箱、密码）在 auth.users，这里只放我们自己的字段。"""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(sa.Text)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    avatar_url: Mapped[str | None] = mapped_column(sa.Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class ProjectMember(Base):
    """谁在哪个项目里、什么角色。一个用户可属于多个项目。"""

    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    # owner / admin / member / viewer，取值由数据库的 check 约束兜底
    role: Mapped[str] = mapped_column(sa.Text, nullable=False, default="viewer")
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("project_id", "user_id", name="project_members_project_id_user_id_key"),
        sa.Index("idx_members_user", "user_id"),
        sa.Index("idx_members_project", "project_id"),
    )

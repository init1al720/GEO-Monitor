"""ORM 基类。

只放所有模型共用的东西：声明式基类与通用列类型。
具体表见 app/models/geo.py。
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# Postgres 用 jsonb（可索引、可查询），SQLite 退化为 JSON，便于本地跑测试
JsonType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """所有模型的基类。"""

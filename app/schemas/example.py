"""示例模块的出入参：演示「入参与出参分开定义」的约定。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="名称")
    description: str | None = Field(default=None, max_length=200, description="描述")


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    created_at: datetime

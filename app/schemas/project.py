"""项目相关出入参：触发采集、重算指标、查看运行记录。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CollectIn(BaseModel):
    engines: list[str] | None = Field(
        default=None,
        description="只跑指定引擎，如 ['deepseek','qwen']；不传则跑所有已配密钥的引擎",
    )
    max_prompts: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="本轮最多跑多少个问题，不传用服务端默认上限",
    )


class CollectAccepted(BaseModel):
    """采集是异步的，这里只确认已排上队，进度看 runs。"""

    project_id: uuid.UUID
    run_id: uuid.UUID = Field(description="本次采集的批次 id，用它轮询运行记录")
    engines: list[str]
    max_prompts: int
    message: str = "采集已开始，进度可查询运行记录"


class MetricsIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="重算最近几天")
    metric_date: date | None = Field(default=None, description="只重算某一天，给了就忽略 days")


class MetricsOut(BaseModel):
    project_id: uuid.UUID
    days: int
    rows: int = Field(description="写入或更新的指标行数")


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    prompt_count: int
    engine_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

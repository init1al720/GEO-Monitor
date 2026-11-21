"""业务时间：报告按客户所在时区切天，不是按 UTC。

为什么需要这一层：看板上的"今日提及率"是按自然日聚合的。如果直接用
UTC 日期，中国客户晚上 8 点之后采集的数据会被算进"昨天"，早上看到的
数字和当晚看到的对不上。所以所有的 metric_date 都按 report_timezone
（默认 Asia/Shanghai）切天，落库的 called_at 仍然是 UTC。

另一个收益：用「时间区间」而不是 ``date(called_at) = '2026-11-22'``
来筛选。函数套在列上会让索引失效，而且 SQLite 与 Postgres 的 date()
语义还不完全一致，区间比较两边都对。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_warned = False


def report_tz() -> ZoneInfo:
    """报告时区。配置写错或缺 tzdata 时退回 UTC，不让聚合直接崩。"""
    global _warned
    name = (get_settings().report_timezone or "UTC").strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if not _warned:
            logger.warning("时区 %s 不可用（Windows 需要 pip install tzdata），退回 UTC", name)
            _warned = True
        return ZoneInfo("UTC")


def business_date(moment: datetime) -> date:
    """把一个时间点换算成业务日。naive 时间按 UTC 解释。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(report_tz()).date()


def business_today() -> date:
    return datetime.now(UTC).astimezone(report_tz()).date()


def day_window(day: date) -> tuple[datetime, datetime]:
    """业务日的 UTC 时间区间 [start, end)，左闭右开。"""
    tz = report_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)

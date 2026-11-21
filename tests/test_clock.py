"""业务时区的切天规则。

这几条如果错了，看板上的"今日"会整体偏 8 小时：
中国客户晚上 9 点看到的数字其实是"昨天"的数据。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.core.clock import business_date, day_window
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _force_tz(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "Asia/Shanghai")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_day_window_is_beijing_midnight() -> None:
    start, end = day_window(date(2026, 11, 22))

    # 北京时间 22 日 00:00 = UTC 21 日 16:00
    assert start == datetime(2026, 11, 21, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 22, 16, 0, tzinfo=UTC)


def test_after_midnight_beijing_counts_as_new_day() -> None:
    # UTC 21 日 19:00 = 北京 22 日 03:00，应该算 22 日
    assert business_date(datetime(2026, 11, 21, 19, 0, tzinfo=UTC)) == date(2026, 11, 22)


def test_naive_datetime_is_read_as_utc() -> None:
    assert business_date(datetime(2026, 11, 21, 19, 0)) == date(2026, 11, 22)


def test_evening_utc_still_same_business_day() -> None:
    # UTC 22 日 15:00 = 北京 22 日 23:00，还是 22 日
    assert business_date(datetime(2026, 11, 22, 15, 0, tzinfo=UTC)) == date(2026, 11, 22)


def test_bad_timezone_falls_back_to_utc(monkeypatch) -> None:
    monkeypatch.setenv("REPORT_TIMEZONE", "Mars/Olympus")
    get_settings.cache_clear()

    start, end = day_window(date(2026, 11, 22))
    assert start == datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 23, 0, 0, tzinfo=UTC)

"""连接串归一化。

Supabase 后台给的连接串带 ``?sslmode=require``，asyncpg 不认这个参数，
会把整串当会话参数发给服务端然后报错。这一步必须翻译掉。
"""

from __future__ import annotations

import pytest

from app.core.db import _normalize_url


def test_sqlite_passthrough() -> None:
    url, args = _normalize_url("sqlite+aiosqlite:///./data/geo.db")
    assert url == "sqlite+aiosqlite:///./data/geo.db"
    assert args == {}


def test_sync_postgres_driver_is_switched_to_asyncpg() -> None:
    url, _ = _normalize_url("postgresql://u:p@h:5432/geo")
    assert url.startswith("postgresql+asyncpg://")


def test_postgres_scheme_alias() -> None:
    url, _ = _normalize_url("postgres://u:p@h:5432/geo")
    assert url.startswith("postgresql+asyncpg://")


def test_sslmode_is_translated_not_forwarded() -> None:
    url, args = _normalize_url("postgresql://u:p@db.x.supabase.co:5432/postgres?sslmode=require")

    assert "sslmode" not in url
    assert args == {"ssl": True}


def test_sslmode_disable() -> None:
    _, args = _normalize_url("postgresql://u:p@h:5432/db?sslmode=disable")
    assert args == {"ssl": False}


def test_other_query_params_are_kept() -> None:
    url, _ = _normalize_url("postgresql://u:p@h:5432/db?application_name=geo")
    assert "application_name=geo" in url


def test_unknown_sslmode_rejected() -> None:
    with pytest.raises(ValueError, match="sslmode"):
        _normalize_url("postgresql://u:p@h:5432/db?sslmode=weird")


def test_password_is_not_mangled() -> None:
    # 密码里的 @ 必须保持 URL 编码，不能被我拆错
    url, _ = _normalize_url("postgresql://u:p%40ss@h:5432/db")
    assert "p%40ss" in url

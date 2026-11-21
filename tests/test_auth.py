"""鉴权与项目权限的单测。

不连 Supabase：用 HS256 自签 token 模拟它签发的形状，数据库用临时 SQLite。
覆盖两类最容易出问题的地方：

- token 校验：过期、签名不对、aud/iss 不对、缺 sub，都必须拒绝；
- 角色判定：viewer 只能看，member 才能触发采集；非成员按 404 处理。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import require_project_role
from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import AuthUser, reset_jwks_cache, verify_token
from app.models.auth import ProjectMember
from app.models.base import Base
from app.models.geo import Project
from app.services.membership import CAN_WRITE_DATA, get_role, list_memberships

SECRET = "test-secret-please-change"  # noqa: S105 - 测试用，不是真密钥
ISS = "https://test.supabase.co/auth/v1"
SUB = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def jwt_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把配置指向一个假的 Supabase 项目，测完还原，避免污染其他用例。"""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    get_settings.cache_clear()
    reset_jwks_cache()
    yield
    get_settings.cache_clear()
    reset_jwks_cache()


def _token(
    *,
    sub: str | None = SUB,
    aud: str = "authenticated",
    iss: str = ISS,
    secret: str = SECRET,
    exp_delta: int = 3600,
    extra: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "aud": aud,
        "iss": iss,
        "role": "authenticated",
        "email": "qh@example.com",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if sub is not None:
        payload["sub"] = sub
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# token 校验
# ---------------------------------------------------------------------------


async def test_valid_token_returns_user(jwt_env) -> None:  # type: ignore[no-untyped-def]
    user = await verify_token(_token())

    assert user.id == SUB
    assert user.email == "qh@example.com"
    assert user.role == "authenticated"
    assert user.is_service is False


async def test_display_name_falls_back_to_metadata(jwt_env) -> None:  # type: ignore[no-untyped-def]
    user = await verify_token(_token(extra={"user_metadata": {"display_name": "陈启航"}}))
    assert user.display_name == "陈启航"


async def test_email_prefix_used_when_no_metadata(jwt_env) -> None:  # type: ignore[no-untyped-def]
    assert (await verify_token(_token())).display_name == "qh"


async def test_expired_token_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="已过期"):
        await verify_token(_token(exp_delta=-60))


async def test_wrong_secret_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="token 无效"):
        await verify_token(_token(secret="someone-elses-secret"))


async def test_wrong_audience_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="token 无效"):
        await verify_token(_token(aud="anon"))


async def test_wrong_issuer_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="token 无效"):
        await verify_token(_token(iss="https://evil.example.com/auth/v1"))


async def test_missing_sub_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="sub"):
        await verify_token(_token(sub=None))


async def test_garbage_token_rejected(jwt_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError, match="格式"):
        await verify_token("not-a-jwt")


async def test_unconfigured_auth_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(UnauthorizedError, match="未配置"):
            await verify_token(_token())
    finally:
        get_settings.cache_clear()
        reset_jwks_cache()


# ---------------------------------------------------------------------------
# 项目角色
# ---------------------------------------------------------------------------


@pytest.fixture
async def session(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/auth.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _project_with_role(session, role: str, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
    project = Project(name="测试项目", brand="智标枪", aliases=[], competitors=["广联达"])
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user_id, role=role))
    await session.flush()
    return project


async def test_get_role_returns_member_role(session) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid.uuid4()
    project = await _project_with_role(session, "member", user_id)

    assert await get_role(session, project.id, user_id) == "member"


async def test_get_role_returns_none_for_outsider(session) -> None:  # type: ignore[no-untyped-def]
    project = await _project_with_role(session, "member", uuid.uuid4())

    assert await get_role(session, project.id, uuid.uuid4()) is None


async def test_list_memberships_returns_projects(session) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid.uuid4()
    project = await _project_with_role(session, "owner", user_id)

    rows = await list_memberships(session, user_id)

    assert len(rows) == 1
    assert rows[0][0].id == project.id
    assert rows[0][1] == "owner"


async def test_viewer_cannot_trigger_collect(session) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid.uuid4()
    project = await _project_with_role(session, "viewer", user_id)
    dependency = require_project_role(*CAN_WRITE_DATA)

    with pytest.raises(ForbiddenError, match="viewer"):
        await dependency(
            project_id=project.id,
            user=AuthUser(id=str(user_id), email="v@example.com", role="authenticated"),
            session=session,
        )


async def test_member_can_trigger_collect(session) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid.uuid4()
    project = await _project_with_role(session, "member", user_id)
    dependency = require_project_role(*CAN_WRITE_DATA)

    access = await dependency(
        project_id=project.id,
        user=AuthUser(id=str(user_id), email="m@example.com", role="authenticated"),
        session=session,
    )

    assert access.project_id == project.id
    assert access.role == "member"


async def test_outsider_gets_404_not_403(session) -> None:  # type: ignore[no-untyped-def]
    """非成员按 404 处理，避免泄露项目是否存在。"""
    user_id = uuid.uuid4()
    project = await _project_with_role(session, "member", uuid.uuid4())
    dependency = require_project_role(*CAN_WRITE_DATA)

    with pytest.raises(NotFoundError):
        await dependency(
            project_id=project.id,
            user=AuthUser(id=str(user_id), email="x@example.com", role="authenticated"),
            session=session,
        )


async def test_non_uuid_subject_rejected(session) -> None:  # type: ignore[no-untyped-def]
    dependency = require_project_role(*CAN_WRITE_DATA)

    with pytest.raises(UnauthorizedError, match="UUID"):
        await dependency(
            project_id=uuid.uuid4(),
            user=AuthUser(id="service_role", email=None, role="service_role"),
            session=session,
        )


# ---------------------------------------------------------------------------
# HTTP 层：没带 token 必须是 401
# ---------------------------------------------------------------------------


def test_me_requires_token(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_collect_requires_token(client) -> None:  # type: ignore[no-untyped-def]
    response = client.post(f"/api/v1/projects/{uuid.uuid4()}/collect", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"

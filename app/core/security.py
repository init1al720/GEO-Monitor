"""Supabase JWT 校验。

认证本身不经过我们的服务器：注册、登录、刷新 token 全在前端直连 Supabase，
我们只负责**验证**它签发的 token，从中取出 user_id。

两种签名都要支持，因为 Supabase 新旧项目不一样：

- HS256：早期项目 / 自建，密钥是 Dashboard 里的 JWT Secret；
- ES256 / RS256：新项目默认，公钥从 `{url}/auth/v1/.well-known/jwks.json` 拉，
  按 token 头里的 kid 选公钥，缓存 10 分钟，kid 未命中时强制刷新一次
  （Supabase 轮换密钥后，旧缓存会失效）。

所有失败一律抛 401，且不区分"token 过期"和"签名不对"的具体原因给外部看，
只给一句可读提示，避免帮攻击者探测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger

logger = get_logger(__name__)

# JWKS 缓存时长。Supabase 换密钥不频繁，10 分钟足够，也给了强制刷新的兜底。
_JWKS_TTL_SECONDS = 600.0

_jwks_keys: dict[str, jwt.PyJWK] = {}
_jwks_fetched_at: float = 0.0


@dataclass(frozen=True)
class AuthUser:
    """通过校验的调用者。字段都来自 token，不查库。"""

    id: str
    email: str | None = None
    role: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_service(self) -> bool:
        """service_role 是我们自己的后端密钥，不应该出现在浏览器里。"""
        return self.role == "service_role"

    @property
    def display_name(self) -> str | None:
        meta = self.claims.get("user_metadata") or {}
        name = meta.get("display_name") if isinstance(meta, dict) else None
        return name or (self.email.split("@")[0] if self.email else None)


async def verify_token(token: str) -> AuthUser:
    """校验 token 并返回调用者。失败抛 UnauthorizedError。"""
    settings = get_settings()
    if not settings.auth_configured:
        raise UnauthorizedError("服务端未配置 Supabase 鉴权（SUPABASE_URL 或 SUPABASE_JWT_SECRET）")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("token 格式不正确") from exc

    algorithm = header.get("alg") or ""
    if algorithm == "HS256":
        key: Any = _hs256_key()
    else:
        key = await _key_from_jwks(header.get("kid"), algorithm)

    options: dict[str, Any] = {"verify_aud": bool(settings.supabase_jwt_audience)}
    kwargs: dict[str, Any] = {
        "algorithms": [algorithm],
        "options": options,
    }
    if settings.supabase_jwt_audience:
        kwargs["audience"] = settings.supabase_jwt_audience
    if settings.supabase_issuer:
        kwargs["issuer"] = settings.supabase_issuer

    try:
        payload: dict[str, Any] = jwt.decode(token, key, **kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("token 校验失败 | %s", exc)
        raise UnauthorizedError("token 无效") from exc

    subject = payload.get("sub")
    if not subject:
        raise UnauthorizedError("token 缺少 sub 声明")

    return AuthUser(
        id=str(subject),
        email=payload.get("email"),
        role=payload.get("role"),
        claims=payload,
    )


def _hs256_key() -> str:
    secret = get_settings().supabase_jwt_secret
    if not secret:
        raise UnauthorizedError("token 用的是 HS256 签名，但服务端没配 SUPABASE_JWT_SECRET")
    return secret


async def _key_from_jwks(kid: str | None, algorithm: str) -> Any:
    """按 kid 取公钥；缓存里没有就强制刷新一次再试。"""
    if not kid:
        raise UnauthorizedError("token 缺少 kid，无法定位签名公钥")
    if not algorithm:
        raise UnauthorizedError("token 缺少 alg")

    keys = await _load_jwks()
    if kid not in keys:
        keys = await _load_jwks(force=True)
    jwk = keys.get(kid)
    if jwk is None:
        logger.warning("JWKS 中找不到 kid=%s，可能是密钥轮换或 token 来自别的项目", kid)
        raise UnauthorizedError("token 签名无法验证")

    if jwk.algorithm_name and jwk.algorithm_name != algorithm:
        raise UnauthorizedError("token 算法与公钥不匹配")
    return jwk.key


async def _load_jwks(*, force: bool = False) -> dict[str, jwt.PyJWK]:
    global _jwks_keys, _jwks_fetched_at

    now = time.monotonic()
    fresh = _jwks_keys and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS
    if fresh and not force:
        return _jwks_keys

    url = f"{get_settings().supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("拉取 JWKS 失败 | %s | %s", url, exc)
        # 拉不到但手里有旧 key 时，继续用旧的，别把在线的请求全打死
        if _jwks_keys:
            return _jwks_keys
        raise UnauthorizedError("暂时无法验证登录状态，请稍后重试") from exc

    keys: dict[str, jwt.PyJWK] = {}
    for item in payload.get("keys", []):
        key_id = item.get("kid")
        if not key_id:
            continue
        try:
            keys[key_id] = jwt.PyJWK(item)
        except jwt.InvalidTokenError as exc:  # pragma: no cover - 只在远端返回脏数据时触发
            logger.warning("JWKS 里有无法解析的公钥 | kid=%s | %s", key_id, exc)

    _jwks_keys = keys
    _jwks_fetched_at = now
    logger.info("JWKS 已刷新 | keys=%s", len(keys))
    return keys


def reset_jwks_cache() -> None:
    """测试用：清掉公钥缓存。"""
    global _jwks_keys, _jwks_fetched_at
    _jwks_keys = {}
    _jwks_fetched_at = 0.0

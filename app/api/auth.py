"""Authentication: scrypt password hashes, HS256 JWTs and the pre-tenant login lookup.

The browser never decides authorization: the tenant comes from the verified token and is anchored
per transaction by `tenant_transaction`, exactly like every other request. Login uses two
exact-match, single-row openings (migration 0005) and answers identically for an unknown firm, an
unknown user and a wrong password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text

from app.settings import Settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD = 10
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_DUMMY_SALT = b"\x00" * 16


class AuthError(Exception):
    """Credentials are not valid. Deliberately says nothing about WHICH part was wrong."""


class TokenError(Exception):
    """The bearer token is missing, malformed, expired or not signed by us."""


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    email: str
    display_name: str
    preferred_language: str


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must have at least {MIN_PASSWORD} characters")
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    salt_b64, digest_b64 = base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_b64}${digest_b64}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time; a missing hash still costs one scrypt so timing does not reveal it."""
    try:
        _, n, r, p, salt, digest = (stored or "").split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        hashlib.scrypt(password.encode(), salt=_DUMMY_SALT, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        return False


def issue_token(p: Principal, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims = {
        "sub": str(p.user_id),
        "tenant_id": str(p.tenant_id),
        "role": p.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, settings: Settings) -> tuple[uuid.UUID, uuid.UUID, str]:
    try:
        c = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
        return uuid.UUID(c["sub"]), uuid.UUID(c["tenant_id"]), str(c["role"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise TokenError(str(exc)) from exc


def authenticate(engine: Engine, slug: str, email: str, password: str) -> Principal:
    """Raises AuthError for anything wrong; never reveals which part."""
    email = email.strip().lower()
    if not SLUG.match(slug):
        verify_password(password, None)
        raise AuthError
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.login_slug', :s, true)"), {"s": slug})
        tenant_id = conn.execute(
            text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug}
        ).scalar()
        if tenant_id is None:
            verify_password(password, None)
            raise AuthError
        conn.execute(text("SELECT set_config('app.login_slug', '', true)"))
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        row = conn.execute(
            text(
                "SELECT id, role, email, display_name, preferred_language, password_hash "
                "FROM users WHERE email = :e"
            ),
            {"e": email},
        ).one_or_none()
    if row is None or not verify_password(password, row.password_hash):
        if row is None:
            verify_password(password, None)
        raise AuthError
    return Principal(
        user_id=row.id, tenant_id=tenant_id, role=row.role, email=row.email,
        display_name=row.display_name, preferred_language=row.preferred_language,
    )  # fmt: skip

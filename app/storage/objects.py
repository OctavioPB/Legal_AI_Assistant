"""Per-tenant object storage (WS7.T01): keys live under `{tenant_id}/`, URLs are scoped to ONE key,
and the IAM policy mirrors RLS. The real guarantee is the bucket policy; this layer makes it
impossible for application code to even ask for another tenant's key.
"""

from __future__ import annotations

import json
import posixpath
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from app.db.session import PUBLIC_CORPUS_TENANT_ID

MAX_TTL = timedelta(minutes=15)  # presigned URLs are short-lived by policy


class ObjectKeyError(ValueError):
    """The requested object name could escape the tenant prefix or is malformed."""


class ObjectBackend(Protocol):
    """The slice of an S3-like service the store needs. `presign` returns a URL that grants ONE
    method on ONE exact key until `expires_at`."""

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def list(self, prefix: str) -> list[str]: ...

    def presign(self, key: str, *, method: str, expires_at: datetime) -> str: ...


def validate_name(name: str) -> str:
    """A relative, normalized object name: no traversal, no absolute path, no control chars."""
    if not name or len(name) > 512 or any(ord(c) < 32 or c in "\\" for c in name):
        raise ObjectKeyError(f"invalid object name: {name!r}")
    if name.startswith("/") or ".." in name.split("/") or "//" in name:
        raise ObjectKeyError(f"object name escapes the tenant prefix: {name!r}")
    if posixpath.normpath(name) != name:
        raise ObjectKeyError(f"object name is not normalized: {name!r}")
    return name


class TenantObjectStore:
    def __init__(self, backend: ObjectBackend, tenant_id: uuid.UUID) -> None:
        if tenant_id == PUBLIC_CORPUS_TENANT_ID:
            raise ObjectKeyError("PUBLIC_CORPUS files are not tenant objects")
        self._backend = backend
        self._prefix = f"{tenant_id}/"

    def key(self, name: str) -> str:
        return self._prefix + validate_name(name)

    def put(self, name: str, data: bytes) -> str:
        key = self.key(name)
        self._backend.put(key, data)
        return key

    def get(self, name: str) -> bytes:
        return self._backend.get(self.key(name))

    def delete(self, name: str) -> None:
        self._backend.delete(self.key(name))

    def list(self) -> list[str]:
        return [k.removeprefix(self._prefix) for k in self._backend.list(self._prefix)]

    def presign_get(self, name: str, *, now: datetime, ttl: timedelta = MAX_TTL) -> str:
        if not timedelta(0) < ttl <= MAX_TTL:
            raise ObjectKeyError(f"ttl must be within (0, {MAX_TTL}]")
        return self._backend.presign(self.key(name), method="GET", expires_at=now + ttl)


def iam_policy(bucket: str, tenant_id: uuid.UUID) -> dict[str, object]:
    """Least-privilege policy for a tenant principal: its prefix and nothing else (RLS mirror)."""
    arn = f"arn:aws:s3:::{bucket}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "TenantObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"{arn}/{tenant_id}/*"],
            },
            {
                "Sid": "TenantListing",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [arn],
                "Condition": {"StringLike": {"s3:prefix": [f"{tenant_id}/*"]}},
            },
            {
                "Sid": "NothingElse",
                "Effect": "Deny",
                "Action": "s3:*",
                "NotResource": [arn, f"{arn}/{tenant_id}/*"],
            },
        ],
    }


def policy_json(bucket: str, tenant_id: uuid.UUID) -> str:
    return json.dumps(iam_policy(bucket, tenant_id), indent=2, sort_keys=True)

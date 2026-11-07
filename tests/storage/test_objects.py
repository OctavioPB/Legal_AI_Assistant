"""A presigned URL of tenant A cannot serve an object of tenant B (WS7.T01)."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta

import pytest

from app.db.session import PUBLIC_CORPUS_TENANT_ID
from app.storage.objects import (
    MAX_TTL,
    ObjectKeyError,
    TenantObjectStore,
    iam_policy,
    validate_name,
)

NOW = datetime(2026, 11, 7, 12, 0, 0)
SECRET = b"test-signing-secret"


class FakeS3:
    """Signs URLs as `key|method|expires|hmac` and serves ONLY what the signature covers."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

    def presign(self, key: str, *, method: str, expires_at: datetime) -> str:
        body = f"{key}|{method}|{expires_at.isoformat()}"
        return body + "|" + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()

    def fetch(self, url: str, now: datetime, key_override: str | None = None) -> bytes:
        key, method, exp, sig = url.rsplit("|", 3)
        good = hmac.new(SECRET, f"{key}|{method}|{exp}".encode(), hashlib.sha256).hexdigest()
        if (
            not hmac.compare_digest(sig, good)
            or now >= datetime.fromisoformat(exp)
            or method != "GET"
        ):
            raise PermissionError("403")
        return self.objects[key_override or key]


@pytest.fixture
def world() -> tuple[FakeS3, TenantObjectStore, TenantObjectStore]:
    s3 = FakeS3()
    a, b = TenantObjectStore(s3, uuid.uuid4()), TenantObjectStore(s3, uuid.uuid4())
    a.put("exp1/contrato.pdf", b"A-secret")
    b.put("exp1/contrato.pdf", b"B-secret")  # same name, different tenant
    return s3, a, b


def test_same_name_in_two_tenants_never_collides(world) -> None:  # type: ignore[no-untyped-def]
    _, a, b = world
    assert a.get("exp1/contrato.pdf") == b"A-secret" and b.get("exp1/contrato.pdf") == b"B-secret"
    assert a.list() == ["exp1/contrato.pdf"] and b.list() == ["exp1/contrato.pdf"]


def test_a_presigned_url_serves_its_own_object_and_nothing_else(world) -> None:  # type: ignore[no-untyped-def]
    s3, a, b = world
    url = a.presign_get("exp1/contrato.pdf", now=NOW)
    assert s3.fetch(url, NOW) == b"A-secret"
    b_key = b.key("exp1/contrato.pdf")
    # tampering with the key inside A's URL breaks the signature
    forged = url.replace(a.key("exp1/contrato.pdf"), b_key)
    with pytest.raises(PermissionError):
        s3.fetch(forged, NOW)
    # ...and a signed URL is only ever for the signed key: it cannot be redirected to B's object
    assert s3.fetch(url, NOW) != s3.objects[b_key]


def test_urls_expire(world) -> None:  # type: ignore[no-untyped-def]
    s3, a, _ = world
    url = a.presign_get("exp1/contrato.pdf", now=NOW, ttl=timedelta(minutes=5))
    assert s3.fetch(url, NOW + timedelta(minutes=4)) == b"A-secret"
    with pytest.raises(PermissionError):
        s3.fetch(url, NOW + timedelta(minutes=5))


def test_ttl_is_bounded(world) -> None:  # type: ignore[no-untyped-def]
    _, a, _ = world
    with pytest.raises(ObjectKeyError):
        a.presign_get("x.pdf", now=NOW, ttl=MAX_TTL + timedelta(seconds=1))
    with pytest.raises(ObjectKeyError):
        a.presign_get("x.pdf", now=NOW, ttl=timedelta(0))


@pytest.mark.parametrize(
    "name",
    [
        "../other-tenant/x.pdf",
        "/abs/x.pdf",
        "a/../../b",
        "a//b",
        "a\\b",
        "",
        "a/./b",
        "x\x00y",
        "a/..",
        "./a",
    ],
)
def test_names_that_could_leave_the_prefix_are_rejected(name: str, world) -> None:  # type: ignore[no-untyped-def]
    _, a, _b = world
    for op in (a.get, a.delete, lambda n: a.put(n, b"x"), lambda n: a.presign_get(n, now=NOW)):
        with pytest.raises(ObjectKeyError):
            op(name)
    assert validate_name("exp1/contrato.pdf")


def test_a_foreign_full_key_is_just_a_name_under_the_callers_prefix(world) -> None:  # type: ignore[no-untyped-def]
    s3, a, b = world
    foreign = b.key("exp1/contrato.pdf")
    with pytest.raises(
        KeyError
    ):  # resolved as A-prefix/B-uuid/exp1/...: does not exist, not B's data
        a.get(foreign)
    assert s3.objects[b.key("exp1/contrato.pdf")] == b"B-secret"


def test_public_corpus_has_no_tenant_object_store() -> None:
    with pytest.raises(ObjectKeyError):
        TenantObjectStore(FakeS3(), PUBLIC_CORPUS_TENANT_ID)


def test_iam_policy_mirrors_rls_one_prefix_and_denies_the_rest() -> None:
    t = uuid.uuid4()
    pol = iam_policy("legalai-files", t)
    stmts = {s["Sid"]: s for s in pol["Statement"]}  # type: ignore[index]
    assert stmts["TenantObjects"]["Resource"] == [f"arn:aws:s3:::legalai-files/{t}/*"]
    assert stmts["TenantListing"]["Condition"]["StringLike"]["s3:prefix"] == [f"{t}/*"]
    assert stmts["NothingElse"]["Effect"] == "Deny"
    allowed = " ".join(str(s["Resource"]) for s in pol["Statement"] if s["Effect"] == "Allow")  # type: ignore[union-attr]
    assert "*/" not in allowed and "legalai-files/*" not in allowed  # never the whole bucket
    assert iam_policy("legalai-files", t) != iam_policy("legalai-files", uuid.uuid4())

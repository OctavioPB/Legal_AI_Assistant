"""WS1.T05 — users, sessions, dossiers, audit_log under RLS (CRUD) and the audit log's immutability."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import AuditLog, ChatSession, Dossier, User
from app.db.session import tenant_session, tenant_transaction
from tests import helpers


def _make_user(engine: Engine, tenant: uuid.UUID, email: str = "ana@firma.mx") -> uuid.UUID:
    with tenant_session(engine, tenant) as s:
        user = User(tenant_id=tenant, email=email, display_name="Ana")
        s.add(user)
        s.flush()
        return user.id


def _jurisdiction(engine: Engine, tenant: uuid.UUID, *key: str | None) -> int:
    with tenant_transaction(engine, tenant) as conn:
        return helpers.jurisdiction_id(conn, *key)  # type: ignore[arg-type]


def test_crud_roundtrip_under_rls(
    app_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    user_id = _make_user(app_engine, tenant_a)
    nl = _jurisdiction(app_engine, tenant_a, "MX", "state", "NL", None)

    with tenant_session(app_engine, tenant_a) as s:
        sess = ChatSession(
            tenant_id=tenant_a,
            user_id=user_id,
            language="es-MX",
            jurisdiction_id=nl,
            persona="labor",
            workflow="dossier_builder",
        )
        s.add(sess)
        s.flush()
        dossier = Dossier(
            tenant_id=tenant_a,
            user_id=user_id,
            session_id=sess.id,
            title="Rescisión",
            workflow="dossier_builder",
            content={"sections": []},
        )
        s.add(dossier)
        s.add(
            AuditLog(
                tenant_id=tenant_a,
                user_id=user_id,
                session_id=sess.id,
                event_type="query",
                query_text="¿causales?",
                retrieved_cite_ids=["MX-FED-LFT-ART47-v2025.06"],
                model_alias="drafting-primary",
                resolved_model_id="fake-model-1",
            )
        )
        s.flush()
        dossier_id = dossier.id

    with tenant_session(app_engine, tenant_a) as s:  # read back + update
        d = s.get(Dossier, dossier_id)
        assert d is not None and d.title == "Rescisión" and d.status == "draft"
        d.status = "final"
    with tenant_session(app_engine, tenant_a) as s:
        assert s.get(Dossier, dossier_id).status == "final"  # type: ignore[union-attr]
        assert s.query(AuditLog).count() == 1

    # tenant B sees none of it, can neither update nor delete it
    with tenant_session(app_engine, tenant_b) as s:
        assert s.get(Dossier, dossier_id) is None
        assert (
            s.query(User).count() == s.query(ChatSession).count() == s.query(AuditLog).count() == 0
        )
    with tenant_transaction(app_engine, tenant_b) as conn:
        assert conn.execute(text("UPDATE dossiers SET title = 'pwned'")).rowcount == 0
        assert conn.execute(text("DELETE FROM dossiers")).rowcount == 0

    with tenant_session(app_engine, tenant_a) as s:  # delete
        s.delete(s.get(Dossier, dossier_id))
    with tenant_session(app_engine, tenant_a) as s:
        assert s.get(Dossier, dossier_id) is None


def test_sessions_hold_catalog_keys_only(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    user_id = _make_user(app_engine, tenant_a)
    federal = _jurisdiction(app_engine, tenant_a, "MX", "federal", None, None)
    bad = [
        dict(
            persona="made_up_persona",
            workflow="dossier_builder",
            language="es-MX",
            jurisdiction_id=federal,
        ),
        dict(
            persona="labor",
            workflow="free_text_workflow",
            language="es-MX",
            jurisdiction_id=federal,
        ),
        dict(
            persona="labor", workflow="dossier_builder", language="fr-FR", jurisdiction_id=federal
        ),
        dict(
            persona="labor", workflow="dossier_builder", language="es-MX", jurisdiction_id=999_999
        ),
    ]
    for values in bad:
        with pytest.raises(IntegrityError):
            with tenant_session(app_engine, tenant_a) as s:
                s.add(ChatSession(tenant_id=tenant_a, user_id=user_id, **values))  # type: ignore[arg-type]


def test_a_tenant_cannot_reference_another_tenants_user(
    app_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    a_user = _make_user(app_engine, tenant_a)
    federal = _jurisdiction(app_engine, tenant_b, "MX", "federal", None, None)
    with pytest.raises(IntegrityError, match="sessions_user_fk"):
        with tenant_session(app_engine, tenant_b) as s:
            s.add(
                ChatSession(
                    tenant_id=tenant_b,
                    user_id=a_user,
                    language="es-MX",
                    jurisdiction_id=federal,
                    persona="labor",
                    workflow="dossier_builder",
                )
            )
    with pytest.raises(IntegrityError, match="dossiers_user_fk"):
        with tenant_session(app_engine, tenant_b) as s:
            s.add(
                Dossier(tenant_id=tenant_b, user_id=a_user, title="x", workflow="dossier_builder")
            )


def test_email_is_unique_per_tenant_only(
    app_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    _make_user(app_engine, tenant_a)
    _make_user(app_engine, tenant_b)  # the same address in another firm is fine (and invisible)
    with pytest.raises(IntegrityError, match="users_email_uq"):
        _make_user(app_engine, tenant_a)
    with pytest.raises(IntegrityError):
        _make_user(app_engine, tenant_a, email="Mixed@Case.mx")  # emails are stored lowercase


def _audit_row(engine: Engine, tenant: uuid.UUID) -> None:
    with tenant_session(engine, tenant) as s:
        s.add(AuditLog(tenant_id=tenant, event_type="refusal", query_text="q"))


def test_audit_log_is_append_only_for_everyone(
    app_engine: Engine, migrator_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    _audit_row(app_engine, tenant_a)
    # the app role: no UPDATE/DELETE/TRUNCATE privilege
    for sql in (
        "UPDATE audit_log SET query_text = 'edited'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            with tenant_transaction(app_engine, tenant_a) as conn:
                conn.execute(text(sql))
    # the owner is subject to FORCE RLS: it has no UPDATE/DELETE policy, so it changes 0 rows
    with migrator_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)})
        assert conn.execute(text("UPDATE audit_log SET query_text = 'edited'")).rowcount == 0
        assert conn.execute(text("DELETE FROM audit_log")).rowcount == 0
    # a superuser bypasses RLS, so only the trigger stands in the way - and it does, for every verb
    for sql in (
        "UPDATE audit_log SET query_text = 'edited'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as conn:
            conn.execute(text(sql))
    with pytest.raises(DBAPIError, match="append-only"):  # TRUNCATE also stops the owner
        with migrator_engine.begin() as conn:
            conn.execute(text("TRUNCATE audit_log"))
    with tenant_session(app_engine, tenant_a) as s:
        assert s.query(AuditLog).count() == 1


def test_audit_rows_are_tenant_private_and_unforgeable(
    app_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    _audit_row(app_engine, tenant_a)
    with tenant_session(app_engine, tenant_b) as s:
        assert s.query(AuditLog).count() == 0
    with pytest.raises(DBAPIError, match="row-level security"):  # B cannot write a row into A's log
        _audit_row_as(app_engine, anchor=tenant_b, row_tenant=tenant_a)


def _audit_row_as(engine: Engine, anchor: uuid.UUID, row_tenant: uuid.UUID) -> None:
    with tenant_session(engine, anchor) as s:
        s.add(AuditLog(tenant_id=row_tenant, event_type="admin"))

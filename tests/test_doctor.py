"""The doctor must be green on a healthy database AND red when an invariant is sabotaged.
Sabotage happens inside a transaction that is rolled back, so nothing leaks into other tests."""

from __future__ import annotations

import uuid

from sqlalchemy import Connection, Engine, text

from app import doctor
from app.settings import Settings
from tests import helpers


def failed(checks: list[doctor.Check]) -> list[str]:
    return [c.name for c in checks if c.status == "FAIL"]


def test_healthy_database_has_no_failures(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        results = doctor.run_all(conn, Settings())
        conn.rollback()
    assert failed(results) == []
    warns = {c.name for c in results if c.status == "WARN"}
    assert any("aliases" in w for w in warns) and any("tau" in w for w in warns)  # honest warnings


def sabotage(admin_engine: Engine, statement: str, check: object) -> list[str]:
    with admin_engine.connect() as conn:
        conn.execute(text(statement))
        names = failed(check(conn))  # type: ignore[operator]
        conn.rollback()
    return names


def test_unforced_rls_is_detected(admin_engine: Engine) -> None:
    names = sabotage(
        admin_engine, "ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY", doctor.check_rls
    )
    assert names == ["RLS ENABLE+FORCE and policies on chunks"]


def test_disabled_rls_is_detected(admin_engine: Engine) -> None:
    names = sabotage(
        admin_engine, "ALTER TABLE dossiers DISABLE ROW LEVEL SECURITY", doctor.check_rls
    )
    assert names == ["RLS ENABLE+FORCE and policies on dossiers"]


def test_a_bypassrls_app_role_is_detected(admin_engine: Engine) -> None:
    names = sabotage(admin_engine, "ALTER ROLE legalai_app BYPASSRLS", doctor.check_roles)
    assert names == ["role legalai_app has no privileged attributes"]


def test_a_superuser_app_role_is_detected(admin_engine: Engine) -> None:
    names = sabotage(admin_engine, "ALTER ROLE corpus_ingestor SUPERUSER", doctor.check_roles)
    assert names == ["role corpus_ingestor has no privileged attributes"]


def test_ddl_privilege_for_the_app_role_is_detected(admin_engine: Engine) -> None:
    names = sabotage(
        admin_engine, "GRANT CREATE ON SCHEMA public TO legalai_app", doctor.check_roles
    )
    assert "legalai_app cannot CREATE in schema public" in names


def test_a_fail_open_policy_is_detected_by_behaviour(admin_engine: Engine) -> None:
    # a permissive extra policy makes `tenants` visible without any tenant context
    stmt = "CREATE POLICY leak ON tenants FOR SELECT TO legalai_app USING (true)"
    names = sabotage(admin_engine, stmt, doctor.check_behaviour)
    assert "fail-closed: no tenant context sees nothing (not even PUBLIC)" in names


def test_two_live_generations_are_detected(
    app_engine: Engine,
    ingestor_engine: Engine,
    admin_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with admin_engine.connect() as conn:
        assert failed(doctor.check_generations(conn)) == []
        conn.execute(
            text("UPDATE chunks SET embedding_revision = 'r-other' WHERE cite_id LIKE '%ART47%'")
        )
        names = failed(doctor.check_generations(conn))
        conn.rollback()
    assert names == ["one embedding/chunker generation is live (invariant 5)"]


def test_a_broken_search_config_is_detected(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        conn.execute(
            text(
                "ALTER TEXT SEARCH CONFIGURATION legal_es DROP MAPPING FOR asciiword, word, hword, hword_part, asciihword, hword_asciipart"
            )
        )
        names = failed(doctor.check_search_config(conn))
        conn.rollback()
    assert any("legal_es" in n for n in names)


def test_the_cli_reports_and_sets_the_exit_code(capsys) -> None:  # type: ignore[no-untyped-def]
    code = doctor.main([])
    out = capsys.readouterr().out
    assert code == 0 and "passed" in out and "failed" in out


def unused(_: Connection) -> None:  # keeps the Connection import meaningful for type checkers
    return None

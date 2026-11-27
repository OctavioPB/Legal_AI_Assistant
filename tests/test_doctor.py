"""The doctor must be green on a healthy database AND red when an invariant is sabotaged.
Sabotage happens inside a transaction that is rolled back, so nothing leaks into other tests."""

from __future__ import annotations

import uuid

from sqlalchemy import Connection, Engine, text

from app import doctor
from app.orchestration.aliases import AliasRegistry
from app.settings import Settings
from tests import helpers


def failed(checks: list[doctor.Check]) -> list[str]:
    return [c.name for c in checks if c.status == "FAIL"]


def test_healthy_database_has_no_failures(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        results = doctor.run_all(conn, Settings(), AliasRegistry())  # a blank, unpinned stack
        conn.rollback()
    assert failed(results) == []
    warns = {c.name for c in results if c.status == "WARN"}
    assert any("aliases" in w for w in warns) and any("tau" in w for w in warns)  # honest warnings


def test_the_doctor_checks_the_pins_and_tau_the_api_actually_loads() -> None:
    """Regression: it checked a blank registry and a tau-less Settings, so it warned "unpinned" and
    "tau is None" about a stack whose pins and calibration record were both in place."""
    from app.main import load_tau

    by_name = {c.name: c for c in doctor.check_repo(load_tau(Settings()))}
    assert by_name["LLM aliases pinned to verified model ids"].status == "PASS"
    assert by_name["refusal tau calibrated (ADR-007)"].status == "PASS"


def test_the_ocr_check_passes_with_tesseract_and_warns_without_it() -> None:
    """ADR-030: scanned uploads need Tesseract with spa+eng; without it they fail (WARN, not FAIL)."""
    ok = doctor.check_ocr(Settings())
    assert ok.status == "PASS", ok.detail
    gone = doctor.check_ocr(Settings(ocr_tesseract_cmd="Z:/no/tesseract.exe"))
    assert gone.status == "WARN"
    unknown = doctor.check_ocr(Settings(ocr_languages="spa+xyz"))
    assert unknown.status == "WARN" and "xyz" in unknown.detail


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


def test_the_cli_reports_and_sets_the_exit_code(capsys, migrated_db: str) -> None:  # type: ignore[no-untyped-def]
    # The migrated test database, never the configured one: in CI that one exists (docker initdb)
    # but is never migrated, so the doctor found no tables and the test failed only there.
    code = doctor.main(["--database", migrated_db])
    out = capsys.readouterr().out
    assert code == 0 and "passed" in out and "failed" in out


def unused(_: Connection) -> None:  # keeps the Connection import meaningful for type checkers
    return None


def test_the_reform_watcher_checks(admin_engine: Engine, ingestor_engine: Engine, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ADR-017: never run / stale / blocked sources warn; tau older than the last reform warns."""
    from tests.lawtext import ingest_law, v1, v2

    def by_name(conn: Connection) -> dict[str, doctor.Check]:
        return {c.name: c for c in doctor.check_reforms(conn, tmp_path / "tau.yaml")}

    with admin_engine.connect() as conn:
        assert by_name(conn)["reform watcher has run (ADR-017)"].status == "WARN"
    ingest_law(ingestor_engine, v1())
    ingest_law(ingestor_engine, v2(), "2026.10.01")
    with admin_engine.begin() as conn:
        pub = "00000000-0000-0000-0000-000000000001"
        conn.execute(text(
            "INSERT INTO source_checks (tenant_id, official_id, source, outcome) VALUES "
            f"('{pub}', 'LFT', 'https://x', 'blocked'), ('{pub}', 'CPF', 'https://y', 'unchanged')"
        ))  # fmt: skip
        ids = conn.execute(text("SELECT id FROM documents ORDER BY ingested_at")).scalars().all()
        conn.execute(text(
            "INSERT INTO corpus_changes (tenant_id, official_id, old_document_id, new_document_id, "
            f"source_uri, counts) VALUES ('{pub}', 'LFT', :a, :b, 'x', '{{}}')"
        ), {"a": ids[0], "b": ids[1]})  # fmt: skip
    (tmp_path / "tau.yaml").write_text("tau: 0.5\ncalibrated_on: '20200101'\n", encoding="utf-8")
    with admin_engine.connect() as conn:
        got = by_name(conn)
    assert got["reform watcher has run (ADR-017)"].status == "PASS"
    assert got["official sources reachable by the watcher"].status == "WARN"
    assert "LFT" in got["official sources reachable by the watcher"].detail
    assert got["tau calibrated after the last reform (ADR-007/017)"].status == "WARN"
    (tmp_path / "tau.yaml").write_text("tau: 0.5\ncalibrated_on: '29991231'\n", encoding="utf-8")
    with admin_engine.connect() as conn:
        assert by_name(conn)["tau calibrated after the last reform (ADR-007/017)"].status == "PASS"

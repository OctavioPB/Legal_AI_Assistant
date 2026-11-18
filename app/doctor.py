"""`uv run python -m app.doctor`: the project invariants as executable checks (WS0.T09).

Every rule in CLAUDE.md / .claude/rules that CAN be checked against a running database or the
repo is checked here, so "RLS is forced" or "one embedding generation is live" is a command, not
a belief. Exit code 1 on any FAIL; WARN means "works but is not demo/production ready".
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import Connection, create_engine, text

from app.orchestration.aliases import AliasRegistry
from app.settings import Settings, get_settings

Status = Literal["PASS", "WARN", "FAIL"]
APP_ROLES = ("legalai_app", "legalai_migrator", "corpus_ingestor", "legalai_provisioner")
EXTENSIONS = ("vector", "ltree", "unaccent", "pgcrypto")
TENANT_COLUMN_EXCEPTIONS = {"tenants"}  # tenants holds the tenant id in `id`


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""


def _tenant_tables(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
        )
    ).scalars()
    return sorted({*rows, *TENANT_COLUMN_EXCEPTIONS})


def check_extensions(conn: Connection) -> list[Check]:
    have = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    out = [Check(f"extension {e}", "PASS" if e in have else "FAIL") for e in EXTENSIONS]
    ver = conn.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if ver:
        major_minor = tuple(int(p) for p in str(ver).split(".")[:2])
        ok = major_minor >= (0, 8)  # hnsw.iterative_scan (ADR-011)
        out.append(Check("pgvector >= 0.8 (iterative scan)", "PASS" if ok else "FAIL", f"v{ver}"))
    return out


def check_roles(conn: Connection) -> list[Check]:
    out: list[Check] = []
    rows = {
        r.rolname: r
        for r in conn.execute(
            text("SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles")
        )
    }
    for role in APP_ROLES:
        r = rows.get(role)
        if r is None:
            out.append(Check(f"role {role} exists", "FAIL"))
            continue
        bad = [n for n, v in (("SUPERUSER", r.rolsuper), ("BYPASSRLS", r.rolbypassrls),
                              ("CREATEDB", r.rolcreatedb), ("CREATEROLE", r.rolcreaterole)) if v]  # fmt: skip
        out.append(
            Check(
                f"role {role} has no privileged attributes",
                "FAIL" if bad else "PASS",
                ",".join(bad),
            )
        )
    owned = conn.execute(
        text(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = 'legalai_app'"
        )
    ).scalar_one()
    out.append(
        Check("legalai_app owns no tables (no DDL)", "PASS" if owned == 0 else "FAIL", str(owned))
    )
    can_ddl = conn.execute(
        text("SELECT has_schema_privilege('legalai_app', 'public', 'CREATE')")
    ).scalar_one()
    out.append(Check("legalai_app cannot CREATE in schema public", "FAIL" if can_ddl else "PASS"))
    return out


def check_rls(conn: Connection) -> list[Check]:
    out: list[Check] = []
    for table in _tenant_tables(conn):
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).one_or_none()
        policies = conn.execute(
            text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = :t"),
            {"t": table},
        ).scalar_one()
        if row is None:
            out.append(Check(f"RLS on {table}", "FAIL", "table missing"))
            continue
        ok = row.relrowsecurity and row.relforcerowsecurity and policies >= 1
        out.append(
            Check(
                f"RLS ENABLE+FORCE and policies on {table}",
                "PASS" if ok else "FAIL",
                f"enabled={row.relrowsecurity} forced={row.relforcerowsecurity} policies={policies}",
            )
        )
    return out


def check_behaviour(conn: Connection) -> list[Check]:
    """Behaviour, not just catalog flags: act as the app role and try to break the rules."""
    out: list[Check] = []
    conn.execute(text("SET LOCAL ROLE legalai_app"))
    try:
        seen = conn.execute(text("SELECT count(*) FROM tenants")).scalar_one()
        out.append(
            Check(
                "fail-closed: no tenant context sees nothing (not even PUBLIC)",
                "PASS" if seen == 0 else "FAIL",
                f"{seen} rows",
            )
        )
        try:
            with conn.begin_nested():
                conn.execute(text("UPDATE audit_log SET output_text = 'x'"))
            out.append(
                Check("audit_log rejects UPDATE for the app role", "FAIL", "update was allowed")
            )
        except Exception:
            out.append(Check("audit_log rejects UPDATE for the app role", "PASS"))
        try:
            with conn.begin_nested():
                conn.execute(text("CREATE TABLE doctor_probe (x int)"))
            out.append(Check("app role cannot run DDL", "FAIL", "CREATE TABLE succeeded"))
        except Exception:
            out.append(Check("app role cannot run DDL", "PASS"))
    finally:
        conn.execute(text("RESET ROLE"))
    return out


# (config, document word, query word): each pair differs only by accent/case. One word per probe,
# and the query must keep its term: a config that drops the term would otherwise "match" on the rest.
_FOLD_PROBES = (
    ("legal_es", "artículo", "ARTICULO"),
    ("legal_es", "rescisión", "rescision"),
    ("legal_es", "Trabajador", "trabajador"),
    ("legal_en", "Retaliate", "RETALIATE"),
)


def check_search_config(conn: Connection) -> list[Check]:
    out: list[Check] = []
    for cfg, doc, q in _FOLD_PROBES:
        try:
            row = conn.execute(
                text(
                    f"SELECT to_tsvector('{cfg}', :d) @@ websearch_to_tsquery('{cfg}', :q), "
                    f"numnode(websearch_to_tsquery('{cfg}', :q))"
                ),
                {"d": doc, "q": q},
            ).one()
            ok = bool(row[0]) and row[1] > 0
            out.append(
                Check(
                    f"lexical arm folds accents and case ({cfg}: {doc} ~ {q})",
                    "PASS" if ok else "FAIL",
                )
            )
        except Exception as exc:
            out.append(Check(f"text search config {cfg}", "FAIL", str(exc).splitlines()[0]))
    return out


def check_generations(conn: Connection) -> list[Check]:
    from app.ops.tripwires import collect_metrics

    m = collect_metrics(conn)
    return [
        Check("one embedding/chunker generation is live (invariant 5)",
              "PASS" if m["live_generations"] <= 1 else "FAIL", f"{int(m['live_generations'])} live"),
        Check("no chunk exceeds the embedder window (ADR-012)",
              "PASS" if m["chunks_exceeding_window"] == 0 else "WARN",
              f"{int(m['chunks_exceeding_window'])} would be truncated"),
    ]  # fmt: skip


def check_repo(settings: Settings, registry: AliasRegistry | None = None) -> list[Check]:
    """`registry` defaults to the pins the API loads (config/models.local.yaml): checking a blank
    registry reported "unpinned" for a stack that was in fact pinned."""
    from app.orchestration.aliases import ALL_ALIASES
    from app.orchestration.pins import load_registry
    from app.prompts.composer import ManifestError, verify_manifest

    out: list[Check] = []
    try:
        verify_manifest()
        out.append(Check("prompt versions match their pinned hashes", "PASS"))
    except ManifestError as exc:
        out.append(Check("prompt versions match their pinned hashes", "FAIL", str(exc)))
    reg = registry if registry is not None else load_registry()
    unpinned = [a for a in ALL_ALIASES if not reg.spec(a).pinned]
    out.append(Check("LLM aliases pinned to verified model ids", "WARN" if unpinned else "PASS",
                     f"unpinned: {', '.join(unpinned)}" if unpinned else ""))  # fmt: skip
    out.append(Check("refusal tau calibrated (ADR-007)", "WARN" if settings.refusal_tau is None else "PASS",
                     "tau is None: the gate refuses to run" if settings.refusal_tau is None else f"tau={settings.refusal_tau}"))  # fmt: skip
    return out


WATCH_STALE_DAYS = 3  # the watcher runs daily; three missed days is worth a warning


def check_reforms(conn: Connection, tau_path: Path | None = None) -> list[Check]:
    """ADR-017: the reform watcher ran recently, no source is blocked, and tau was calibrated on the
    corpus in use (a reform changes the scores tau was measured on)."""
    out: list[Check] = []
    last = conn.execute(
        text(
            "SELECT max(checked_at) AS at, now() - max(checked_at) > make_interval(days => :d) AS old "
            "FROM source_checks"
        ),
        {"d": WATCH_STALE_DAYS},
    ).one()
    if last.at is None:
        out.append(Check("reform watcher has run (ADR-017)", "WARN",
                         "never: python -m app.ingestion.watch (dev-up.ps1 -ScheduleReforms)"))  # fmt: skip
    else:
        out.append(Check("reform watcher has run (ADR-017)", "WARN" if last.old else "PASS",
                         f"last check {last.at:%Y-%m-%d %H:%M}"))  # fmt: skip
    blocked = (
        conn.execute(
            text(
                "SELECT official_id FROM (SELECT DISTINCT ON (official_id) official_id, outcome "
                "FROM source_checks WHERE official_id NOT LIKE 'news:%' "
                "ORDER BY official_id, checked_at DESC) x "
                "WHERE outcome IN ('blocked','failed') ORDER BY official_id"
            )
        )
        .scalars()
        .all()
    )
    out.append(Check("official sources reachable by the watcher", "WARN" if blocked else "PASS",
                     f"blocked/failed: {', '.join(blocked)}" if blocked else ""))  # fmt: skip
    path = tau_path or Path(__file__).resolve().parents[1] / "config" / "retrieval.local.yaml"
    changed = conn.execute(text("SELECT max(detected_at)::date FROM corpus_changes")).scalar()
    if changed is not None and path.exists():
        import yaml

        calibrated = str((yaml.safe_load(path.read_text("utf-8")) or {}).get("calibrated_on", ""))
        stale = calibrated < changed.strftime("%Y%m%d")
        out.append(Check("tau calibrated after the last reform (ADR-007/017)",
                         "WARN" if stale else "PASS",
                         f"tau {calibrated}, corpus changed {changed:%Y%m%d}: "
                         "re-run python -m evals.calibrate_tau" if stale else ""))  # fmt: skip
    return out


def check_news(conn: Connection) -> list[Check]:
    """ADR-027: the news feed was refreshed recently (it runs with the daily reform watcher). Press
    sources are not legal sources: one of them failing is reported here, never as a blocked law."""
    last = conn.execute(
        text(
            "SELECT max(refreshed_at) AS at, now() - max(refreshed_at) > make_interval(days => :d) "
            "AS old FROM news_items"
        ),
        {"d": WATCH_STALE_DAYS},
    ).one()
    if last.at is None:
        return [Check("news feed refreshed (ADR-027)", "WARN",
                      "never: python -m app.news.pipeline (runs with dev-up.ps1 -WatchReforms)")]  # fmt: skip
    return [Check("news feed refreshed (ADR-027)", "WARN" if last.old else "PASS",
                  f"last refresh {last.at:%Y-%m-%d %H:%M}")]  # fmt: skip


DB_CHECKS: tuple[Callable[[Connection], list[Check]], ...] = (
    check_extensions, check_roles, check_rls, check_behaviour, check_search_config, check_generations,
    check_reforms, check_news,
)  # fmt: skip


def run_all(
    conn: Connection, settings: Settings, registry: AliasRegistry | None = None
) -> list[Check]:
    results: list[Check] = []
    for fn in DB_CHECKS:
        results.extend(fn(conn))
    results.extend(check_repo(settings, registry))
    return results


def main(argv: list[str] | None = None) -> int:
    from app.main import load_tau  # tau from the calibration record, exactly as the API loads it

    settings = load_tau(get_settings())
    engine = create_engine(settings.superuser_url())
    try:
        with engine.connect() as conn:
            results = run_all(conn, settings)
            conn.rollback()
    except Exception as exc:
        sys.stdout.write(
            f"FAIL  cannot reach the database: {exc}\n  start it: docker compose up -d postgres\n"
        )
        return 1
    finally:
        engine.dispose()
    width = max(len(c.name) for c in results)
    for c in results:
        sys.stdout.write(f"{c.status:4}  {c.name:<{width}}  {c.detail}\n")
    counts = {s: sum(c.status == s for c in results) for s in ("PASS", "WARN", "FAIL")}
    sys.stdout.write(
        f"\n{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed\n"
    )
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

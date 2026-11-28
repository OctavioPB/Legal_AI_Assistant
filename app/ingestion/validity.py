"""ADR-050: correct overlapping validity periods between versions of the same law.

    uv run python -m app.ingestion.validity            # show what would change (nothing written)
    uv run python -m app.ingestion.validity --apply    # write the corrections

Before ADR-050 a reform set the old version's `valid_to` to the day the watcher DETECTED it, which
can be after the new version's `valid_from` (the CPF: old version "in force" until 2026-11-25, new
one from 2026-11-13). The correction ends the older version where the newer one begins, never
before its own start. Only dates change; no row is deleted (invariant 3). It runs as the
superuser, an explicit and reviewed data step (multi-tenancy.md): public versions are invisible
to the migrator under FORCE RLS.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any

from sqlalchemy import Connection, create_engine, text


@dataclass(frozen=True)
class Fix:
    document_id: str
    official_id: str
    version_label: str
    old_valid_to: date
    new_valid_to: date


def overlaps(conn: Connection) -> list[Fix]:
    """Consecutive versions (by ingestion order) where the older one ends after the newer one
    begins."""
    rows = conn.execute(
        text(
            "SELECT id::text AS id, tenant_id, official_id, version_label, valid_from, valid_to "
            "FROM documents WHERE official_id IS NOT NULL "
            "ORDER BY tenant_id, official_id, ingested_at"
        )
    ).all()
    out: list[Fix] = []
    for prev, nxt in pairwise(rows):
        if (prev.tenant_id, prev.official_id) != (nxt.tenant_id, nxt.official_id):
            continue
        if prev.valid_to is None or nxt.valid_from is None or prev.valid_to <= nxt.valid_from:
            continue
        end = max(nxt.valid_from, prev.valid_from) if prev.valid_from else nxt.valid_from
        out.append(Fix(prev.id, prev.official_id, prev.version_label, prev.valid_to, end))
    return out


def apply(conn: Connection, fixes: list[Fix]) -> int:
    for f in fixes:
        conn.execute(
            text("UPDATE documents SET valid_to = :v WHERE id = CAST(:d AS uuid)"),
            {"v": f.new_valid_to, "d": f.document_id},
        )
    return len(fixes)


def main(argv: list[str] | None = None, out: Any = sys.stdout) -> int:
    from app.settings import get_settings

    ap = argparse.ArgumentParser(prog="app.ingestion.validity")
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    a = ap.parse_args(argv)
    engine = create_engine(get_settings().superuser_url())
    try:
        with engine.begin() as conn:
            fixes = overlaps(conn)
            for f in fixes:
                out.write(
                    f"{f.official_id} {f.version_label}: "
                    f"valid_to {f.old_valid_to} -> {f.new_valid_to}\n"
                )
            if not fixes:
                out.write("no overlapping versions\n")
            elif a.apply:
                out.write(f"corrected {apply(conn, fixes)} version(s)\n")
            else:
                out.write("dry run: nothing written (use --apply)\n")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

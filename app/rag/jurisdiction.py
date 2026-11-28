"""The ONLY place jurisdiction scope is decided (CLAUDE.md WHAT, jurisdictions.md).

Hard SQL pre-filter, applied inside both retrieval arms, never post-filtering.
Stacking goes UP and never sideways:
  municipal -> that municipality + its state + federal
  state     -> that state + federal
  federal   -> federal only
always within the session's country, `is_current` unless the session asks for history, and
navigation chunks (índices) excluded.

ADR-025, same hard pre-filter: a chunk that belongs to a matter (a client file) is searched only by
a session of THAT matter. RLS already hides other people's matters; this keeps one client's file
out of another client's conversation even when the lawyer is a member of both.
"""

from __future__ import annotations

from typing import NamedTuple

from app.rag.context import SessionContext


class Predicate(NamedTuple):
    sql: str  # a boolean expression over `chunks` columns; all values are bind parameters
    params: dict[str, object]


def build_jurisdiction_predicate(ctx: SessionContext, *, alias: str = "chunks") -> Predicate:
    a = alias
    params: dict[str, object] = {"j_country": ctx.country}
    scopes = [f"{a}.level = 'federal'"]
    if ctx.level in ("state", "municipal"):
        if ctx.state_code is None:
            raise ValueError("a state/municipal session needs state_code")
        params["j_state"] = ctx.state_code
        scopes.append(f"({a}.level = 'state' AND {a}.state_code = :j_state)")
    if ctx.level == "municipal":
        if ctx.municipality is None:
            raise ValueError("a municipal session needs municipality")
        params["j_muni"] = ctx.municipality
        scopes.append(
            f"({a}.level = 'municipal' AND {a}.state_code = :j_state "
            f"AND {a}.municipality = :j_muni)"
        )
    parts = [f"{a}.country = :j_country", "(" + " OR ".join(scopes) + ")", f"NOT {a}.is_navigation"]
    if ctx.as_of is not None:
        # ADR-050: the version in force on that date, whatever is current today. A version whose
        # validity is unknown is never assumed to cover it (refused, not guessed); a firm document
        # is not a version of a law and is always searched.
        params["j_asof"] = ctx.as_of.isoformat()  # a string: the predicate is logged as JSON
        parts.append(
            f"{a}.document_id IN (SELECT d.id FROM documents d WHERE d.doc_type = 'client_file' "
            "OR (d.valid_from IS NOT NULL AND d.valid_from <= CAST(:j_asof AS date) "
            "AND (d.valid_to IS NULL OR CAST(:j_asof AS date) < d.valid_to)))"
        )
    elif not ctx.include_historical:
        parts.append(f"{a}.is_current")
    if ctx.matter_id is None:
        parts.append(f"{a}.matter_id IS NULL")
    else:
        params["j_matter"] = str(ctx.matter_id)  # a string: the predicate is logged as JSON
        parts.append(f"({a}.matter_id IS NULL OR {a}.matter_id = CAST(:j_matter AS uuid))")
    return Predicate(" AND ".join(parts), params)

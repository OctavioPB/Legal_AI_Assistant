"""The ONLY place jurisdiction scope is decided (CLAUDE.md WHAT, jurisdictions.md).

Hard SQL pre-filter, applied inside both retrieval arms, never post-filtering.
Stacking goes UP and never sideways:
  municipal -> that municipality + its state + federal
  state     -> that state + federal
  federal   -> federal only
always within the session's country, `is_current` unless the session asks for history, and
navigation chunks (índices) excluded.
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
    if not ctx.include_historical:
        parts.append(f"{a}.is_current")
    return Predicate(" AND ".join(parts), params)

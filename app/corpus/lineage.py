"""Reform lineage (ADR-017): how a citable unit of an old version maps to the current one.

`cite_lineage` is written by the reform watcher, one row per citable unit of the retired version.
Readers
never guess: an id with no lineage row is either current or simply unknown.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

Change = Literal["unchanged", "amended", "derogated", "removed", "added"]
MAX_HOPS = 20  # a law reformed more often than this between two reads is followed in later calls


class Succession(BaseModel):
    """Where an old id leads today. `current` is None when the unit was removed."""

    model_config = ConfigDict(frozen=True)

    old: str
    current: str | None
    change: Change  # the strongest change along the chain (amended beats unchanged)
    published_on: date | None
    citation_string: str


_RANK = {"unchanged": 0, "added": 0, "amended": 1, "derogated": 2, "removed": 3}

_STEP = text(
    "SELECT l.old_cite_id, l.new_cite_id, l.change, l.citation_string, c.published_on "
    "FROM cite_lineage l JOIN corpus_changes c ON c.id = l.change_id AND c.tenant_id = l.tenant_id "
    "WHERE l.old_cite_id = ANY(:ids)"
)


def successions(conn: Connection, cite_ids: Iterable[str]) -> dict[str, Succession]:
    """Follow every id through all later reforms. Ids without lineage are absent from the result."""
    start = list(dict.fromkeys(cite_ids))
    out: dict[str, Succession] = {}
    frontier = {i: i for i in start}  # current id -> original id
    for _ in range(MAX_HOPS):
        if not frontier:
            break
        rows = conn.execute(_STEP, {"ids": list(frontier)}).all()
        nxt: dict[str, str] = {}
        for r in rows:
            origin = frontier[r.old_cite_id]
            prev = out.get(origin)
            change: Change = r.change
            if prev is not None and _RANK[prev.change] > _RANK[change]:
                change = prev.change
            out[origin] = Succession(
                old=origin, current=r.new_cite_id, change=change,
                published_on=r.published_on, citation_string=r.citation_string,
            )  # fmt: skip
            if r.new_cite_id is not None and r.new_cite_id != r.old_cite_id:
                nxt[r.new_cite_id] = origin
        frontier = nxt
    return out


def carry_forward(conn: Connection, cite_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Carried sources of a conversation (ADR-016) after a reform: an unchanged unit is carried as
    its
    new id; an amended, derogated or removed one is NOT carried silently (the user gets an alert and
    the
    new text must be found by the search itself)."""
    s = successions(conn, cite_ids)
    out: list[str] = []
    for i in cite_ids:
        step = s.get(i)
        if step is None:
            out.append(i)
        elif step.change == "unchanged" and step.current:
            out.append(step.current)
    return tuple(dict.fromkeys(out))

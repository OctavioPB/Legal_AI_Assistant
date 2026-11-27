"""ADR-051: a sentence that credits a decree with a change must match what the decree's list says.

A reform decree's operative article ("Artículo Único.- Se reforman los artículos 2o; 3o; ...; se
adicionan ...; y se derogan los artículos 15-A; 15-B; 15-C ...") is stored as a transitorio unit of
the law. Its citation can exist (provenance) while the sentence misreads it: E2E/golden gd2-024
credited the repeal of art. 15-C to a decree that only added it. This is read by code, never by
the model (invariant 6):

* `decree_changes(text)` parses the list into {verb: article ids}: `reform`, `add`, `repeal`
  (deroga/abroga), and `unknown` for numbers before the first verb (a chunk split mid-list);
* `mismatches(unit, cites, texts)`: if a sentence cites a decree list and says article X was
  repealed / reformed / added, X must be listed under one of the sentence's verbs (or in an
  `unknown` run) in at least one cited decree text. Otherwise the sentence is reported.

Shape only: the article ids are compared as written (number, letter, Bis/Ter), never inferred.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Literal

Change = Literal["reform", "add", "repeal", "unknown"]

_VERB = re.compile(r"\bse\s+(reforma|adiciona|deroga|abroga)n?\b", re.I)
_KIND: dict[str, Change] = {
    "reforma": "reform",
    "adiciona": "add",
    "deroga": "repeal",
    "abroga": "repeal",
}
# an article id as decrees write it: 15A, 15-B, 1004-A, 3o Bis, 123, 2o
_ID_WORD = re.compile(
    # the suffix letter is upper-case only, so "13 y 14" is never read as "13Y"
    r"\b(\d{1,4})(?:\s*(?:o|º|°))?(?:\s*-?\s*((?-i:[A-Z])))?\b"
    r"(\s+(?:bis|ter|quater|quinquies|sexies))?",
    re.I,
)
# the end of the list: the decree's own text that follows it is not a list of articles
_END = re.compile(r"para\s+quedar\s+como\s+sigue", re.I)

# a sentence's claim: which change, of which articles
_CLAIM_VERB = (
    ("repeal", re.compile(r"\b(derog|abrog)", re.I)),
    ("reform", re.compile(r"\breform", re.I)),
    ("add", re.compile(r"\badicion", re.I)),
)
_ART_RUN = re.compile(
    r"\bart(?:[ií]culos?|s?\.)\s*((?:\d{1,4}\s*(?:o|º|°)?\s*(?:-?\s*(?-i:[A-Z])(?![a-z]))?"
    r"(?:\s+(?:bis|ter|quater|quinquies|sexies))?\s*(?:,|;|\by\b|\be\b)?\s*)+)",
    re.I,
)


def _fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()


def _ids(run: str) -> set[str]:
    out: set[str] = set()
    for m in _ID_WORD.finditer(run):
        num, letter, suffix = m.group(1), (m.group(2) or ""), (m.group(3) or "")
        out.add(f"{int(num)}{letter.upper()}{suffix.strip().upper()}")
    return out


def decree_changes(text: str) -> dict[Change, set[str]]:
    """The articles a decree's list reforms, adds and repeals; empty when it is not a list."""
    end = _END.search(text)
    body = text[: end.start()] if end else text
    marks = list(_VERB.finditer(body))
    if not marks:
        return {}
    out: dict[Change, set[str]] = {}
    lead = body[: marks[0].start()]
    if _ids(lead):
        out.setdefault("unknown", set()).update(_ids(lead))
    for k, m in enumerate(marks):
        stop = marks[k + 1].start() if k + 1 < len(marks) else len(body)
        found = _ids(body[m.end() : stop])
        if found:
            out.setdefault(_KIND[m.group(1).lower()], set()).update(found)
    return out


def is_decree_cite(cite_id: str) -> bool:
    """Only a law's transitorio units hold decrees' lists (MX ids carry `-TRANS`)."""
    return "-TRANS" in cite_id.upper()


def claim(unit: str) -> tuple[set[Change], set[str]]:
    """The changes a sentence asserts and the articles it names."""
    folded = _fold(unit)
    verbs: set[Change] = {k for k, rx in _CLAIM_VERB if rx.search(folded)}  # type: ignore[misc]
    arts: set[str] = set()
    for m in _ART_RUN.finditer(unit):
        arts |= _ids(m.group(1))
    return verbs, arts


def mismatches(unit: str, cites: Iterable[str], texts: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Articles this sentence says a cited decree changed, that no cited decree lists so."""
    lists: list[dict[Change, set[str]]] = []
    for c in cites:
        if not is_decree_cite(c):
            continue
        parsed = [d for d in (decree_changes(t) for t in texts.get(c, ())) if d]
        if any("unknown" not in d for d in parsed):
            # the expanded article starts its own list: a chunk's verb-less run defers to it
            parsed = [{k: v for k, v in d.items() if k != "unknown"} for d in parsed]
        lists.extend(d for d in parsed if d)
    if not lists:
        return []
    verbs, arts = claim(unit)
    if not verbs or not arts:
        return []
    bad = []
    for a in sorted(arts):
        ok = any(a in d.get(v, set()) or a in d.get("unknown", set()) for d in lists for v in verbs)
        if not ok:
            bad.append(a)
    return bad

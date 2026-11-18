"""Fixed, explainable relevance (ADR-027): no model decides what a lawyer reads.

score = source weight + legal terms found (capped) + recency.

* The weight puts official sources above press.
* Legal terms come from a closed catalog, matched on folded text (accents and case ignored)
  with word boundaries. Each item keeps the terms that matched, so the UI can say why it is in
  the feed.
* Recency decays with a half-life. An item without a date uses the day it was first seen.

A general outlet's item needs `min_press_terms` of legal weight, or it is not news for this feed.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

# (folded pattern, label shown to the user, weight). Specific terms weigh 1.0; general ones 0.5.
TERMS: tuple[tuple[str, str, float], ...] = (
    (r"reforma[s]?", "reforma", 1.0),
    # the legislative sense only ("iniciativa privada", "tomar la iniciativa" are not news)
    (r"iniciativa[s]? (?:de ley|de reforma|de decreto|presidencial|preferente)", "iniciativa", 1.0),
    (r"decreto[s]?", "decreto", 1.0),
    (r"diario oficial|\bdof", "DOF", 1.0),
    (r"suprema corte|scjn|(?:la|nueva) corte", "Suprema Corte", 1.0),
    (r"amparo[s]?", "amparo", 1.0),
    (r"jurisprudencia[s]?", "jurisprudencia", 1.0),
    (r"tesis aislada", "tesis", 1.0),
    (r"accion(?:es)? de inconstitucionalidad", "acción de inconstitucionalidad", 1.0),
    (r"controversia[s]? constitucional(?:es)?", "controversia constitucional", 1.0),
    (r"inconstitucional(?:es|idad)?", "inconstitucionalidad", 1.0),
    (r"sentencia[s]?", "sentencia", 1.0),
    (r"tribunal(?:es)?", "tribunal", 1.0),
    (r"vinculad[oa]s? a proceso|vinculacion a proceso", "vinculación a proceso", 1.0),
    (r"orden(?:es)? de aprehension", "orden de aprehensión", 1.0),
    (r"poder judicial", "Poder Judicial", 1.0),
    (r"ley(?:es)?", "ley", 0.5),
    (r"codigo[s]?", "código", 0.5),
    (r"congreso|senado|camara de diputados|diputad[oa]s|senador(?:a|es|as)?", "Congreso", 0.5),
    (r"juez(?:a)?|jueces|magistrad[oa]s?|ministr[oa]s? de la corte", "juzgadores", 0.5),
    (r"fiscalia|fiscal general|ministerio publico", "fiscalía", 0.5),
    (r"juicio[s]?|demanda[s]?|litigio[s]?", "juicio", 0.5),
)
_COMPILED = tuple((re.compile(rf"\b(?:{p})\b"), label, w) for p, label, w in TERMS)
TERMS_CAP = 3.0


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()


def legal_terms(text: str) -> tuple[list[str], float]:
    """(labels found, their weight, capped)."""
    t = fold(text)
    found: list[str] = []
    weight = 0.0
    for rx, label, w in _COMPILED:
        if rx.search(t) and label not in found:
            found.append(label)
            weight += w
    return found, min(weight, TERMS_CAP)


def recency(when: datetime, now: datetime, half_life_days: float) -> float:
    age = max(0.0, (now - when).total_seconds() / 86400)
    return 2.0 * math.pow(0.5, age / half_life_days)


@dataclass(frozen=True)
class Scored:
    key: str  # dedupe key (url)
    title: str
    score: float
    terms: tuple[str, ...]
    source: str = ""


def dedupe(items: Iterable[Scored], similarity: float = 0.88) -> list[Scored]:
    """Highest score first; drop an item whose URL was already kept, or whose title is nearly the
    same as one kept from ANOTHER source (the same story in two outlets, a reform seen by the
    watcher and by the index). Within one source only the URL counts: an official index lists
    distinct laws with near-identical titles ("Ley de Ingresos ... 2026" / "... 2027")."""
    kept: list[Scored] = []
    seen: list[tuple[str, str]] = []  # (folded title, source)
    seen_keys: set[str] = set()
    for it in sorted(items, key=lambda s: (-s.score, s.key)):
        t = fold(it.title)
        if it.key in seen_keys or any(
            src != it.source and difflib.SequenceMatcher(None, t, o).ratio() >= similarity
            for o, src in seen
        ):
            continue
        kept.append(it)
        seen_keys.add(it.key)
        seen.append((t, it.source))
    return kept


__all__ = ["TERMS", "Scored", "dedupe", "fold", "legal_terms", "recency"]

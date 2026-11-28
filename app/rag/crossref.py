"""ADR-037 (C): units a retrieved fragment refers to ("en los términos del artículo 48", "el
artículo 123 de la Constitución") are added to the context, after everything else, into the budget
left over.

The references are read by the brief verifier's citation grammar (ADR-019), never by a model. A
reference that names no law means the fragment's own law (the code of its cite_id); one that names
a law uses that law. The units are loaded by `hits_by_stem`, under the SAME jurisdiction predicate,
embedding generation and vigencia filter as the query (a referenced law outside the session never
comes back). They are context, not hits: the tau gate and the recall metrics only see the query's
own hits; they are citable because they are shown (ADR-020).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.corpus.verify import extract, stem
from app.rag.types import Hit


def _law_base(cite_id: str) -> str | None:
    """`MX-FED-LFT` of `MX-FED-LFT-ART47-FRACII-v2026.11`; None for firm documents and US codes
    (the US grammar names sections differently and is not wired here yet)."""
    parts = cite_id.split("-")
    if len(parts) < 4 or parts[0] != "MX" or cite_id.startswith("CF-"):
        return None
    return "-".join(parts[:3])


def _own_article(cite_id: str) -> str | None:
    for part in cite_id.split("-")[3:]:
        if part.startswith("ART"):
            return part
    return None


def referenced_stems(hits: Iterable[Hit], *, limit: int) -> list[str]:
    """Cite-id stems of the units the hits' own text refers to, in order of first mention, without
    the hits' own articles, at most `limit`."""
    own = {(b, a) for h in hits if (b := _law_base(h.cite_id)) and (a := _own_article(h.cite_id))}
    out: list[str] = []
    for h in hits:
        base = _law_base(h.cite_id)
        if base is None:
            continue
        for ref in extract(h.text):
            if ref.article is None:
                continue
            if ref.resolution == "named":
                s = stem(ref)
            elif ref.resolution == "none":  # no law named: the fragment's own law
                s = f"{base}-ART{ref.article}"
                if ref.fraccion:
                    s += f"-FRAC{ref.fraccion}"
            else:  # ambiguous, outside the catalogue, or inferred from context: never guessed
                continue
            if s is None:
                continue
            ref_base, ref_art = s.split("-ART", 1)[0], "ART" + s.split("-ART", 1)[1].split("-")[0]
            if (ref_base, ref_art) in own or s in out:
                continue
            out.append(s)
            if len(out) >= limit:
                return out
    return out

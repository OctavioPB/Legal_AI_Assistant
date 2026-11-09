"""Citation verifier for external briefs (feature 2, ADR-019).

Deterministic code from end to end: references to statutes ("artículo 47, fracción II, de la Ley
Federal
del Trabajo", "arts. 47 y 48 LFT", "Cal. Lab. Code § 201(c)") are extracted with a grammar over a
CLOSED catalog of law names, resolved to the `cite_id`s of the stored corpus, and checked: the unit
exists, it is current (not derogated, not removed by a reform), and any quotation next to it appears
verbatim in it (accent/case/whitespace-folded, at least `min_words` words, same rule as the answer
validator). Nothing is judged by a model and nothing of the brief is stored by this module.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

from app.corpus.lineage import successions
from app.ingestion.corpus import BY_KEY
from app.validation.validator import _ELLIPSIS, fold_words

Status = Literal[
    "vigente",
    "derogado",
    "no_vigente",
    "inexistente",
    "fuera_del_corpus",
    "ley_no_identificada",
    "ambigua",
]
QuoteStatus = Literal["coincide", "no_coincide", "coincide_version_anterior", "corta"]

MAX_TEXT_CHARS = 400_000
MAX_REFS = 400
LAW_WINDOW = 160  # how far after (then before) a reference its law name may appear


@dataclass(frozen=True)
class LawName:
    official_id: str | None  # None: a law we know by name but did not load
    pattern: str
    label: str


# Folded (accent-less, lower-case) names. Longest first: "codigo civil federal" before "codigo
# civil".
_LAWS: tuple[LawName, ...] = (
    LawName("LFT", r"ley\s+federal\s+del\s+trabajo|\blft\b", "Ley Federal del Trabajo"),
    LawName(
        "CPEUM",
        r"constitucion\s+politica\s+de\s+los\s+estados\s+unidos\s+mexicanos|\bcpeum\b"
        r"|constitucion\s+(?:politica\s+)?federal|carta\s+magna"
        r"|\bconstitucion\b(?!\s+(?:politica\s+)?(?:del|de\s+la|para|de)\s+(?:estado|ciudad|nuevo|baja|"
        r"jalisco|california))",
        "Constitución Política de los Estados Unidos Mexicanos",
    ),
    LawName(
        "CC_NL",
        r"codigo\s+civil\s+(?:para\s+el\s+estado\s+de|del\s+estado\s+de|de)\s+nuevo\s+leon",
        "Código Civil para el Estado de Nuevo León",
    ),
    LawName("CCF", r"codigo\s+civil\s+federal|\bccf\b", "Código Civil Federal"),
    LawName("CPF", r"codigo\s+penal\s+federal|\bcpf\b", "Código Penal Federal"),
    LawName(
        "REG_JUSTICIA_CIVICA_MTY",
        r"reglamento\s+de\s+justicia\s+civica(?:\s+del\s+municipio)?\s+de\s+monterrey",
        "Reglamento de Justicia Cívica del Municipio de Monterrey",
    ),
    LawName(
        "REG_ADMIN_MTY",
        r"reglamento\s+de\s+la\s+administracion\s+publica\s+municipal\s+de\s+monterrey",
        "Reglamento de la Administración Pública Municipal de Monterrey",
    ),
    # Known by name, not loaded: reported as outside the corpus, never guessed.
    LawName(None, r"ley\s+del\s+seguro\s+social|\blss\b", "Ley del Seguro Social"),
    LawName(None, r"ley\s+de\s+amparo", "Ley de Amparo"),
    LawName(
        None,
        r"codigo\s+nacional\s+de\s+procedimientos\s+(?:penales|civiles\s+y\s+familiares)",
        "Código Nacional de Procedimientos",
    ),
    LawName(
        None,
        r"codigo\s+federal\s+de\s+procedimientos\s+civiles",
        "Código Federal de Procedimientos Civiles",
    ),
    LawName(
        None,
        r"ley\s+federal\s+de\s+proteccion\s+de\s+datos\s+personales[a-z\s]*",
        "Ley Federal de Protección de Datos Personales",
    ),
    LawName(
        None,
        r"ley\s+del\s+infonavit|ley\s+del\s+instituto\s+del\s+fondo\s+nacional\s+de\s+la\s+vivienda[a-z\s]*",
        "Ley del INFONAVIT",
    ),
    LawName(None, r"codigo\s+de\s+comercio", "Código de Comercio"),
    LawName(
        None, r"codigo\s+fiscal\s+de\s+la\s+federacion|\bcff\b", "Código Fiscal de la Federación"
    ),
)
_AMBIGUOUS = re.compile(r"codigo\s+civil(?!\s+(?:federal|para|del|de\s+nuevo))")
_ANY_LAW = re.compile("|".join(f"(?:{law.pattern})" for law in _LAWS))
_ANAPHORA = re.compile(
    r"(?:de\s+la\s+|del\s+)?(?:citad[ao]|mism[ao]|referid[ao]|invocad[ao]|dich[ao])\s+"
    r"(?:ley|codigo|ordenamiento|cuerpo\s+(?:legal|normativo)|reglamento|constitucion)"
    r"|(?:de\s+la\s+)?ley\s+de\s+la\s+materia"
)

# "47", "47o" (47º folded), "47 bis", "15-C", "153A". A spaced single letter is NOT a suffix ("47 a
# la").
_NUM = r"\d{1,4}(?:o\b)?(?:\s*-?\s*(?:bis|ter|quater|quinquies|sexies)\b|\s*-\s*[a-z]\b|[a-z]\b)?"
_ROMAN = r"[ivxlc]{1,7}"
_ART = re.compile(
    rf"\bart(?:iculos?|s?\.)\s*(?P<nums>{_NUM}(?:\s*(?:,|y|e|al|a)\s*{_NUM})*)"
    rf"(?:\s*,?\s*apartado\s+(?P<apart>[a-z])\b)?"
    rf"(?:\s*,?\s*(?:fracc?(?:ion(?:es)?|\.)|fr\.)\s*(?P<fracs>{_ROMAN}(?:\s*(?:,|y|e)\s*{_ROMAN})*)\b)?"
    rf"(?:\s*,?\s*inciso\s+(?P<inc>[a-z])\)?)?"
)
_US = re.compile(
    r"(?:cal(?:ifornia)?\.?\s*lab(?:or)?\.?\s*code|labor\s+code)\s*,?\s*(?:§|sec(?:tion|\.)?)\s*"
    r"(?P<sec>\d{1,5}(?:\.\d{1,3})?)(?P<subs>(?:\([a-z0-9]{1,4}\))*)"
    r"|(?:§|section)\s*(?P<sec2>\d{1,5}(?:\.\d{1,3})?)(?P<subs2>(?:\([a-z0-9]{1,4}\))*)\s+of\s+the\s+"
    r"(?:california\s+)?labor\s+code"
)
_QUOTES = re.compile(r"[\"“«]([^\"”»]{8,3000})[\"”»]")


def fold_keep_length(s: str) -> str:
    """Lower-case, accent-less, SAME length as `s` (offsets stay valid for highlighting)."""
    out = []
    for ch in s:
        base = unicodedata.normalize("NFKD", ch)[:1] or ch
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


class Ref(BaseModel):
    """One unit a brief cites. `span`: offset of the reference in the brief (response only)."""

    model_config = ConfigDict(frozen=True)

    official_id: str | None
    law_label: str | None
    resolution: Literal["named", "contextual", "none", "ambiguous", "outside"]
    article: str | None  # "47", "47BIS", "15C" (MX) or "201" (US)
    apartado: str | None = None
    fraccion: str | None = None
    inciso: str | None = None
    us_subs: tuple[str, ...] = ()
    span: tuple[int, int]
    quotes: tuple[str, ...] = ()


def _norm_article(raw: str) -> str:
    m = re.match(r"(\d+)(?:o\b)?\s*-?\s*(bis|ter|quater|quinquies|sexies|[a-z]\b)?", raw.strip())
    if not m:
        return raw.upper()
    return m.group(1) + (m.group(2) or "").upper()


def _split_list(raw: str, item: str) -> list[str]:
    parts = re.split(r"\s*(?:,|\by\b|\be\b)\s*", raw)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        rng = re.fullmatch(rf"({item})\s*(?:al|a)\s*({item})", p)
        if rng and item == _NUM and rng.group(1).isdigit() and rng.group(2).isdigit():
            a, b = int(rng.group(1)), int(rng.group(2))
            out.extend(str(i) for i in range(a, min(b, a + 30) + 1))  # "arts. 47 al 50"
        else:
            out.append(p)
    return out


_ABBREV = re.compile(
    r"(?:\b(?:arts?|fracc?|fr|cal|lab|sec|n[uú]m|no|p|pp|inc|lic|dr|dra|sr|sra|"
    r"[a-z])|\d)\.$",
    re.IGNORECASE,
)


def _sentences(brief: str) -> list[tuple[int, int]]:
    """Sentence spans. A terminator inside quotation marks, or after a legal abbreviation
    ("art.", "fracc.", "Cal. Lab."), does not end a sentence."""
    masked = list(brief)
    for q in _QUOTES.finditer(brief):
        for i in range(q.start(), q.end()):
            if masked[i] in ".;:!?\n":
                masked[i] = " "
    text_m = "".join(masked)
    spans: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"[.;!?](?=\s)|:(?=\s*\n)|\n\s*\n", text_m):
        end = m.end()
        if m.group(0) == "." and _ABBREV.search(text_m[max(start, end - 8) : end]):
            continue
        if end > start:
            spans.append((start, end))
        start = end
    if start < len(brief):
        spans.append((start, len(brief)))
    return spans


_LEAD = re.compile(r"\s*,?\s*(?:de\s+la\s+|del\s+|de\s+|la\s+|el\s+|\(\s*)?")
_OUTSIDE = re.compile(
    r"\b(?:ley|codigo|reglamento|constitucion)\s+(?:general|federal|nacional|organica|"
    r"politica|de|del|para|sobre)\b"
)
_MORE_NUMS = re.compile(rf"(?:,|\by\b|\be\b)\s*(?:el\s+|los\s+)?(?:art\w*\.?\s*)?({_NUM})(?=\W)")


def _law_in(window: str) -> tuple[LawName | None, str, int]:
    """The first law name in `window`: (law, how, offset). `how` is named / ambiguous / contextual /
    outside, or none (offset = len(window))."""
    best: tuple[int, LawName | None, str] = (len(window), None, "none")
    for law in _LAWS:
        m = re.search(law.pattern, window)
        if m and m.start() < best[0]:
            best = (m.start(), law, "named" if law.official_id else "outside")
    for rx, how in ((_AMBIGUOUS, "ambiguous"), (_ANAPHORA, "contextual"), (_OUTSIDE, "outside")):
        m = rx.search(window)
        if m and m.start() < best[0]:
            best = (m.start(), None, how)
    return best[1], best[2], best[0]


def extract(brief: str) -> list[Ref]:
    """Every statute reference in the brief, in order of appearance.

    The law of a reference is the first law named after it in the same sentence (before the next
    reference), else the last one named before it in the same sentence, else the law last named in
    the brief (`contextual`: shown as inferred). Quotations are tied to the references of their own
    sentence, or of the previous sentence when that one ends with a colon."""
    brief = brief[:MAX_TEXT_CHARS]
    folded = fold_keep_length(brief)
    sents = _sentences(brief)
    quotes = [(q.start(), q.end(), q.group(1)) for q in _QUOTES.finditer(brief)]
    refs: list[Ref] = []
    last_law: LawName | None = None

    def sentence_of(pos: int) -> int:
        for i, (a, b) in enumerate(sents):
            if a <= pos < b:
                return i
        return len(sents) - 1

    def quotes_for(i: int) -> tuple[str, ...]:
        a, b = sents[i]
        own = [q for qs, qe, q in quotes if a <= qs < b]
        if not own and brief[a:b].rstrip().endswith(":") and i + 1 < len(sents):
            na, nb = sents[i + 1]
            own = [q for qs, qe, q in quotes if na <= qs < nb]
        return tuple(own)

    arts = list(_ART.finditer(folded))
    for k, m in enumerate(arts):
        i = sentence_of(m.start())
        sa, sb = sents[i]
        nxt = arts[k + 1].start() if k + 1 < len(arts) and arts[k + 1].start() < sb else sb
        window = folded[m.end() : min(nxt, m.end() + LAW_WINDOW)]
        law, how, at = _law_in(window)
        if how == "none":
            before = folded[sa : m.start()]
            hits = list(_ANY_LAW.finditer(before))
            if hits:
                for cand in _LAWS:
                    if re.fullmatch(cand.pattern, hits[-1].group(0)):
                        law, how = cand, ("named" if cand.official_id else "outside")
                        break
            elif _ANAPHORA.search(before):  # "La citada ley, en su artículo 48"
                how = "contextual"
        if how in ("contextual", "none") and last_law is not None:
            law, how = last_law, "contextual"
        if law is not None and how == "named":
            last_law = law
        nums = _split_list(m.group("nums"), _NUM)
        nums += [x.group(1) for x in _MORE_NUMS.finditer(window[:at])]
        fracs = _split_list(m.group("fracs"), _ROMAN) if m.group("fracs") else [None]
        q = quotes_for(i)
        for n_i, num in enumerate(nums):
            for fr in fracs if n_i == 0 else [None]:  # "47, fracción II, y 48": II is of 47
                refs.append(
                    Ref(
                        official_id=law.official_id if law else None,
                        law_label=law.label if law else None,
                        resolution=how,  # type: ignore[arg-type]
                        article=_norm_article(num),
                        apartado=(m.group("apart") or None),
                        fraccion=fr.upper() if fr else None,
                        inciso=m.group("inc") or None,
                        span=(m.start(), m.end()),
                        quotes=q,
                    )
                )
                if len(refs) >= MAX_REFS:
                    return refs
    for m in _US.finditer(folded):
        sec = m.group("sec") or m.group("sec2")
        subs = re.findall(r"\(([a-z0-9]{1,4})\)", m.group("subs") or m.group("subs2") or "")
        refs.append(
            Ref(
                official_id="CA_LABOR_CODE",
                law_label="California Labor Code",
                resolution="named",
                article=sec,
                us_subs=tuple(subs),
                span=(m.start(), m.end()),
                quotes=quotes_for(sentence_of(m.start())),
            )
        )
    refs.sort(key=lambda r: r.span)
    return refs[:MAX_REFS]


def stem(ref: Ref) -> str | None:
    """The `cite_id` stem (no version suffix) of the unit the reference names."""
    if ref.official_id is None or ref.article is None:
        return None
    d = BY_KEY[ref.official_id]
    base = f"{d.country}-{d.scope}-{d.code}"
    if d.grammar == "us":
        s = f"{base}-SEC{ref.article.replace('.', 'P')}"
        for sub in ref.us_subs:
            s += f"-SUB{sub.upper()}"
        return s
    s = f"{base}-ART{ref.article}"
    if ref.apartado:
        s += f"-APARTADO{ref.apartado.upper()}"
    if ref.fraccion:
        s += f"-FRAC{ref.fraccion}"
    if ref.inciso:
        s += f"-INC{ref.inciso.upper()}"
    return s


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: str  # normalized human reference, generated here (never the brief's own words)
    official_id: str | None
    status: Status
    quote: QuoteStatus | None
    cite_ids: tuple[str, ...]
    citation_string: str | None
    reform: dict[str, str] | None  # a recorded reform touching the cited unit
    span: tuple[int, int]
    inferred: bool = False  # the law was not named next to the reference: taken from context


def human_ref(ref: Ref) -> str:
    parts: list[str] = []
    if ref.article:
        parts.append(
            ("§ " if ref.official_id == "CA_LABOR_CODE" else "art. ")
            + ref.article
            + "".join(f"({s})" for s in ref.us_subs)
        )
    if ref.apartado:
        parts.append(f"apartado {ref.apartado.upper()}")
    if ref.fraccion:
        parts.append(f"fracc. {ref.fraccion}")
    if ref.inciso:
        parts.append(f"inciso {ref.inciso})")
    if ref.law_label:
        parts.append(ref.law_label)
    return ", ".join(parts)


_DEROGATED = re.compile(r"^\W*(?:art[ií]culo\s+[\w\s-]{1,15}[.\-–:]*\s*)?\(?\s*se\s+deroga", re.I)

_MATCH_SQL = text(
    "SELECT c.cite_id, c.citation_string, c.text, c.is_current, d.valid_to IS NULL AS doc_current, "
    "coalesce(a.full_text, n.full_text) AS unit_text "
    "FROM chunks c JOIN documents d ON d.id = c.document_id JOIN doc_nodes n ON n.id = c.node_id "
    "LEFT JOIN LATERAL (SELECT x.full_text FROM doc_nodes x WHERE x.document_id = n.document_id "
    "AND x.path @> n.path AND x.node_type IN ('articulo','transitorio') "
    "ORDER BY nlevel(x.path) DESC LIMIT 1) a ON true "
    "WHERE d.official_id = :o AND d.doc_type <> 'client_file' AND NOT c.is_navigation "
    "AND (regexp_replace(c.cite_id, '-v[^-]+$', '') = :s "
    "     OR regexp_replace(c.cite_id, '-v[^-]+$', '') LIKE :prefix) "
    "AND regexp_replace(c.cite_id, '-v[^-]+$', '') !~ '-DUP[0-9]+(-|$)' "
    "ORDER BY d.ingested_at DESC, c.char_start, c.cite_id"
)


def _segments(quotes: tuple[str, ...], min_words: int) -> list[str]:
    out = []
    for q in quotes:
        for seg in _ELLIPSIS.split(q):
            f = fold_words(seg).strip(" .,;:")
            if len(f.split()) >= min_words:
                out.append(f)
    return out


def _quote_status(segments: list[str], current: list[str], old: list[str]) -> QuoteStatus:
    cur = [fold_words(t) for t in current]
    prev = [fold_words(t) for t in old]
    verdict: QuoteStatus = "coincide"
    for f in segments:
        if any(f in t for t in cur):
            continue
        if any(f in t for t in prev):
            verdict = "coincide_version_anterior"
        else:
            return "no_coincide"
    return verdict


def verify(conn: Connection, brief: str, *, min_words: int = 5) -> list[Finding]:
    """Resolve and check every reference. `conn` is anchored on the caller's tenant (RLS).

    A quotation next to a list ("arts. 47 y 48 ... «...»") is checked against ALL the units of that
    list: it only has to appear in one of them."""
    refs = extract(brief)
    cache: dict[tuple[str, str], list[Any]] = {}
    resolved: list[tuple[Ref, str, Status | None, list[Any], list[Any]]] = []
    for ref in refs:
        label = human_ref(ref) or "?"
        early: Status | None = None
        if ref.resolution == "outside" or (ref.official_id is None and ref.law_label):
            early = "fuera_del_corpus"
        elif ref.resolution == "ambiguous":
            early = "ambigua"
        s = stem(ref)
        if early is None and (s is None or ref.official_id is None):
            early = "ley_no_identificada"
        current: list[Any] = []
        old: list[Any] = []
        if early is None and s is not None and ref.official_id is not None:
            key = (ref.official_id, s)
            if key not in cache:
                cache[key] = list(
                    conn.execute(
                        _MATCH_SQL, {"o": ref.official_id, "s": s, "prefix": s + "-%"}
                    ).all()
                )
            # the current VERSION; a derogated unit in it is stored with is_current=false (rag.md)
            current = [r for r in cache[key] if r.doc_current]
            old = [r for r in cache[key] if not r.doc_current]
        resolved.append((ref, label, early, current, old))

    texts_by_span: dict[tuple[int, int], tuple[list[str], list[str]]] = {}
    for ref, _, _, current, old in resolved:
        cur_t, old_t = texts_by_span.setdefault(ref.span, ([], []))
        cur_t.extend([r.unit_text for r in current] + [r.text for r in current])
        old_t.extend(r.unit_text for r in old)

    out: list[Finding] = []
    for ref, label, early, current, old in resolved:
        if early is not None:
            out.append(
                Finding(
                    ref=label,
                    official_id=None,
                    status=early,
                    quote=None,
                    cite_ids=(),
                    citation_string=None,
                    reform=None,
                    span=ref.span,
                )
            )
            continue
        if current:
            status: Status = (
                "derogado"
                if all(not r.is_current or _DEROGATED.match(r.text) for r in current)
                else "vigente"
            )
        else:
            status = "no_vigente" if old else "inexistente"
        reform = None
        if old:
            changed = [
                x
                for x in successions(conn, [r.cite_id for r in old]).values()
                if x.change != "unchanged"
            ]
            if changed:
                reform = {
                    "change": changed[0].change,
                    "published_on": changed[0].published_on.isoformat()
                    if changed[0].published_on
                    else "",
                }
        quote: QuoteStatus | None = None
        if ref.quotes:
            segs = _segments(ref.quotes, min_words)
            if not segs:
                quote = "corta"
            elif status in ("vigente", "derogado", "no_vigente"):
                cur_t, old_t = texts_by_span[ref.span]
                quote = _quote_status(segs, cur_t, old_t)
        hits = current or old
        out.append(
            Finding(
                ref=label,
                official_id=ref.official_id,
                status=status,
                quote=quote,
                cite_ids=tuple(r.cite_id for r in hits)[:8],
                citation_string=hits[0].citation_string if hits else None,
                reform=reform,
                span=ref.span,
                inferred=ref.resolution == "contextual",
            )
        )
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    out: dict[str, int] = {"total": len(findings)}
    for f in findings:
        out[f.status] = out.get(f.status, 0) + 1
        if f.quote:
            out[f"quote_{f.quote}"] = out.get(f"quote_{f.quote}", 0) + 1
    return out

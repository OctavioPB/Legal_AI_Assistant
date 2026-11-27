"""Pure citation validator (WS5.T01/T02). No I/O, no model: same input, same Verdict.

Proves PROVENANCE, not faithfulness. A right cite with an inverted summary passes; the NLI/judge
tier is a net whose agreement with lawyer grading is measured before it may gate anything
(blueprint section 12 declares this residual risk).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from app.prompts.composer import EXAMPLE_CITE_IDS, OWN_ANALYSIS_TAG, STRATEGIC_TAG
from app.validation.decrees import mismatches
from app.validation.types import RetrievedSet, ValidationConfig, Verdict

# Every quantifier below is bounded or cannot cross its own delimiter, so an UNCLOSED marker costs
# O(n), never O(n^2): "[cite: " * 50000 (350k chars) once took 241 s with the unbounded forms.
_CITE = re.compile(r"\[\s{0,20}cite\s{0,20}:\s{0,20}([^\]\[]{0,2000})\]", re.IGNORECASE)
_OPEN, _CLOSE = "<insuficiente>", "</insuficiente>"
# A line that is ONLY a markdown heading or bold text is a title. Only explicit markup counts: a
# short unpunctuated sentence is still a claim.
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+\S.*|[-*•]?\s*\*\*[^*\n]{1,200}\*\*:?)\s*$")
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("«", "»"))
_MAX_QUOTE_CHARS = 3000
# A citation right after the period stays with its sentence; a TAG after the period starts the next
# sentence (ADR-021: a tag labels what follows it, never the sentence before it).
_UNIT_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[\s{0,20}cite\s{0,20}:)", re.IGNORECASE)
# Omission marks: "...", "…" and the bracketed legal convention "[...]". The brackets belong to the
# mark, so they must not stay behind as stray characters at the edge of a segment.
_ELLIPSIS = re.compile(r"\[?\s*(?:\.\.\.|…)\s*\]?")

# Workflows whose answers may carry [razonamiento estratégico] instead of a citation (prompts.md):
# the opposing counsel (adversarial) and the user's own advocate (argumentar, ADR-016).
STRATEGIC_WORKFLOWS = frozenset({"adversarial", "argumentar", "hoja_clave"})  # + ADR-028

# Template tokens a model copies from a prompt. Compared after upper-casing.
_PLACEHOLDERS = {"CITE_ID", "X", "ID", "XXX", "...", "…", "N/A", "NA", "NONE", ""} | {
    e.upper() for e in EXAMPLE_CITE_IDS
}

# Refusal-like prose WITHOUT the block is a format violation (folded: no accents, lower case).
_PROSE_REFUSAL = (
    "no encontre",
    "no cuento con",
    "no hay informacion suficiente",
    "no es posible responder",
    "no puedo responder",
    "no se encontro",
    "i could not find",
    "i couldn't find",
    "i cannot find",
    "insufficient information",
    "there is not enough information",
)


# List enumerators ("I.", "XIV)", "a)") are structure, not words: a model that flattens a list
# into a quotation drops them. They are ignored in BOTH the quote and the fragment, so the WORDS
# must still match letter for letter.
_ENUMERATOR = re.compile(r"(?:(?<=\s)|^)(?:[ivxlc]{1,7}[.)]|[a-z]\))(?=\s)")


# Reform annotations the official PDFs print inside an article ("Fracción reformada DOF 30-11-2012",
# "(REFORMADO, P.O. ...)"). They annotate the text, they are not the text: a quotation that skips
# them is still verbatim. The ingestion grammar already treats them as notes.
_ANNOTATION = re.compile(
    r"(?:(?:art[ií]culo|fracci[oó]n|p[aá]rrafo|inciso|apartado|t[ií]tulo|cap[ií]tulo|secci[oó]n)"
    r"(?:es)?\s+(?:reformad|adicionad|derogad|recorrid|modificad)[oa]s?[^\n]*?DOF[^\n]*"
    r"|\((?:reformad|adicionad|derogad|fe de erratas)[^)\n]*\))",
    re.IGNORECASE,
)


def fold_words(text: str) -> str:
    """`fold` minus list enumerators and reform annotations: only for comparing quotations."""
    cleaned = _ANNOTATION.sub(" ", text)
    return re.sub(r"\s+", " ", _ENUMERATOR.sub(" ", fold(cleaned))).strip()


@lru_cache(maxsize=512)
def _fold_cached(text: str) -> str:
    return fold_words(text)


def fold(text: str) -> str:
    """Accent-, case- and whitespace-insensitive form used by every textual comparison."""
    t = unicodedata.normalize("NFKD", text.replace(" ", " "))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace(chr(0x2019), "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", t).strip()


def parse_citations(text: str) -> list[str]:
    """Every cited id in order of first appearance. Accepts grouped ids (`A; B`, `A, B`) and
    tolerates case/whitespace variants of the marker."""
    seen: dict[str, None] = {}
    for m in _CITE.finditer(text):
        for raw in re.split(r"[;,]", m.group(1)):
            seen.setdefault(raw.strip(), None)
    return list(seen)


_MASK = {".": chr(1), "!": chr(2), "?": chr(3)}  # control chars never occur in answers
_UNMASK = {v: k for k, v in _MASK.items()}


def _quote_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each quotation INCLUDING its delimiters, in linear time.

    Uses str.find and remembers the nearest closer, so thousands of unclosed openers cost O(n)
    instead of rescanning the rest of the text for each one (the regex form was quadratic).
    """
    spans: list[tuple[int, int]] = []
    for opener, closer in _QUOTE_PAIRS:
        i, closer_at = 0, -2
        while True:
            o = text.find(opener, i)
            if o < 0:
                break
            c = closer_at if closer_at > o else text.find(closer, o + 1)
            closer_at = c
            if c < 0:
                break
            if 1 <= c - o - 1 <= _MAX_QUOTE_CHARS:
                spans.append((o, c + 1))
                i = c + 1
            else:
                i = o + 1
    return sorted(spans)


def _mask_quotes(line: str) -> str:
    """Sentence terminators inside a quotation (statute text, ellipses) do not end the unit."""
    chars = list(line)
    for start, end in _quote_spans(line):
        for k in range(start, end):
            chars[k] = _MASK.get(chars[k], chars[k])
    return "".join(chars)


# "Art. 291-M", "fracc. IV", "núm. 5": the period after an abbreviation followed by a number or
# numeral does not end a sentence. Only that exact shape is merged, so a real boundary survives.
_ABBREV_END = re.compile(
    r"(?:^|[\s(,;])(?:art|arts|fracc|fr|inc|párr|parr|núm|num|no|sec|secs|cap|tít|tit|cfr|vs|pág|pp|ej)\.$",
    re.IGNORECASE,
)
_NUMBER_START = re.compile(r"^(?:\d|[IVXLC]+|[A-Z]?\d)")


# An inline list ("... menor de un año; II. Si comprueba ...") and an ordinal ("artículo 1°. de la
# Constitución"): the period belongs to the numeral, not to the sentence (seen with the real model:
# a cited sentence was cut in two and its first half reported as uncited). Only these exact shapes
# merge: an enumerator right after ";", ":" or ", y", or an ordinal followed by a lower-case
# continuation.
_INLINE_ENUM_END = re.compile(r"(?:[;:]|,\s*[yeo])\s*(?:[IVXLC]{1,7}|[a-z]\)|\d{1,3})\.?$")
_ORDINAL_END = re.compile(r"\d{1,4}\s?[°º]\.$|\d{1,4}o\.$")
_LOWER_START = re.compile(r"^[a-záéíóúñü]")


def _line_units(line: str) -> list[str]:
    pieces = [
        "".join(_UNMASK.get(ch, ch) for ch in u).strip()
        for u in _UNIT_SPLIT.split(_mask_quotes(line.strip()))
        if u.strip()
    ]
    merged: list[str] = []
    for piece in pieces:
        if merged and (
            (_ABBREV_END.search(merged[-1]) and _NUMBER_START.match(piece))
            or _INLINE_ENUM_END.search(merged[-1])
            or (_ORDINAL_END.search(merged[-1]) and _LOWER_START.match(piece))
        ):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _units(text: str) -> list[str]:
    return [u for line in text.splitlines() if line.strip() for u in _line_units(line)]


def _plain(unit: str) -> str:
    """The unit without cite markers, tags, markdown and list numbering: what a reader counts."""
    t = _CITE.sub("", unit).replace(OWN_ANALYSIS_TAG, "").replace(STRATEGIC_TAG, "")
    t = re.sub(r"^[\s#>*\-•]+|\*\*|__", "", t)
    return re.sub(r"^(?:\(?[IVXLC]+\)|\(?\d+[.)]|\([a-z]\))\s*", "", t.strip())


_FIRST_STOP = re.compile(r"[.!?](?=\s|$)")


def _coverage(
    answer: str, retrieved: RetrievedSet, strategic_ok: bool, min_words: int
) -> tuple[list[str], float]:
    """Every legal-looking sentence needs a cite or an allowed tag.

    A tag ALONE on its line labels the paragraph that follows (seen with the real local model:
    "[análisis propio]" on its own line, then the paragraph), until a blank line or a new heading.
    ADR-021: a tag in the FIRST sentence of a line labels the whole line ("* **[razonamiento
    estratégico] Falacia:** Usted da por sentado... No obstante, ..."), and only that line; a tag
    later in a line still covers only its own sentence.
    """
    uncovered: list[str] = []
    tagged = total = 0
    label: str | None = None  # "own" | "strategic" while a label line is in force
    for line in answer.splitlines():
        if not line.strip():
            label = None
            continue
        if _HEADING_LINE.match(line):  # explicit markup says "title": it asserts nothing
            label = None
            continue
        if not _plain(line).strip():  # a line made only of tags (and markup)
            if OWN_ANALYSIS_TAG in line:
                label = "own"
            elif strategic_ok and STRATEGIC_TAG in line:
                label = "strategic"
            continue
        units = _line_units(line)
        # the line's lead: everything before its first sentence terminator (quotes masked), so a tag
        # written AFTER the first sentence never labels the line
        end = _FIRST_STOP.search(_mask_quotes(line))
        lead = line[: end.start()] if end else line
        line_label = (
            "own"
            if OWN_ANALYSIS_TAG in lead
            else "strategic"
            if strategic_ok and STRATEGIC_TAG in lead
            else None
        )
        for unit in units:
            body = _plain(unit)
            if len(body.split()) < min_words or body.endswith(":"):
                label = None  # a heading/label line starts a new section
                continue
            total += 1
            if any(cid in unit for cid in retrieved.ids):
                continue  # a source listing names its fragment
            has_cite = bool(_CITE.search(unit))
            analysis = OWN_ANALYSIS_TAG in unit or "own" in (label, line_label)
            strategic = strategic_ok and (
                STRATEGIC_TAG in unit or "strategic" in (label, line_label)
            )
            if analysis or strategic:
                tagged += 1
            if not (has_cite or analysis or strategic):
                uncovered.append(body[:140])
    return uncovered, (tagged / total if total else 0.0)


QUOTE_FIX_MIN_RATIO = 0.8  # below this the "closest passage" is a different text: offer none


def closest_passage(quote: str, sources: list[str]) -> str:
    """The exact words of a source that best match a quotation that failed the verbatim check (one
    word changed, an article dropped...). Pure, deterministic; compares folded words, returns the
    source's own spelling. Returns "" when nothing reaches QUOTE_FIX_MIN_RATIO."""
    import difflib

    def toks(t: str) -> list[str]:  # words without the punctuation glued to them
        return [w.strip(".,;:()«»\"'") for w in fold_words(t).split()]

    qw = [w for w in toks(quote) if w]
    if not qw:
        return ""
    best, best_text = 0.0, ""
    n = len(qw)
    for src in sources:
        words = src.split()
        folded = [(toks(w) or [""])[0] for w in words]
        for size in (n - 2, n - 1, n, n + 1, n + 2):
            if size <= 0:
                continue
            for i in range(0, max(1, len(words) - size + 1)):
                window = [w for w in folded[i : i + size] if w]
                r = difflib.SequenceMatcher(None, qw, window, autojunk=False).ratio()
                if r > best:
                    best, best_text = r, " ".join(words[i : i + size])
    return best_text.strip(" ,;:.") if best >= QUOTE_FIX_MIN_RATIO else ""


def _bad_quotes(answer: str, retrieved: RetrievedSet, min_words: int) -> list[str]:
    return [b for b, _ in _bad_quotes_with_fixes(answer, retrieved, min_words)]


def _bad_quotes_with_fixes(
    answer: str, retrieved: RetrievedSet, min_words: int
) -> list[tuple[str, str]]:
    bad: list[tuple[str, str]] = []
    for unit in _units(answer):
        quotes = [unit[s + 1 : e - 1] for s, e in _quote_spans(unit)]
        if not quotes:
            continue
        originals = [
            t for c in parse_citations(unit) if c in retrieved.ids for t in retrieved.texts[c]
        ]
        allowed = [_fold_cached(t) for t in originals]
        for q in quotes:
            for seg in _ELLIPSIS.split(q):
                seg_f = fold_words(seg).strip(" .,;:")
                if len(seg_f.split()) >= min_words and not any(seg_f in a for a in allowed):
                    bad.append((seg.strip()[:140], closest_passage(seg, originals)))
    return bad


def _decree_mismatches(answer: str, retrieved: RetrievedSet) -> list[str]:
    """ADR-051: sentences that credit a cited decree with a change its list does not make."""
    out: list[str] = []
    for unit in _units(answer):
        cites = [c for c in parse_citations(unit) if c in retrieved.ids]
        for art in mismatches(_mask_quotes(unit), cites, retrieved.texts):
            out.append(f"{art}: {_plain(unit)[:140]}")
    return out


GAP = _OPEN + _CLOSE


def normalize_gaps(answer: str) -> tuple[str, int]:
    """Replace every well-formed <insuficiente>...</insuficiente> block with the canonical empty
    one and count them (linear, via str.find). Whatever the model wrote inside is DISCARDED: the
    refusal/gap text is the system's (ADR-007), rendered from the catalog by the UI."""
    low = answer.lower()
    out: list[str] = []
    pos = n = 0
    while True:
        start = low.find(_OPEN, pos)
        end = low.find(_CLOSE, start + len(_OPEN)) if start >= 0 else -1
        if start < 0 or end < 0:
            out.append(answer[pos:])
            return "".join(out), n
        out.append(answer[pos:start])
        out.append(GAP)
        n += 1
        pos = end + len(_CLOSE)


def validate(
    answer: str,
    retrieved: RetrievedSet,
    *,
    workflow: str,
    config: ValidationConfig | None = None,
) -> Verdict:
    cfg = config or ValidationConfig()
    if len(answer) > cfg.max_answer_chars:  # never run the heavier checks on a runaway output
        return Verdict(
            cited=(),
            invalid=(),
            placeholders=(),
            refused=False,
            prose_refusal=False,
            uncovered=(f"output exceeds {cfg.max_answer_chars} characters",),
            bad_quotes=(),
            analysis_share=0.0,
            grounded=False,
        )
    text, gaps = normalize_gaps(answer)
    ids = parse_citations(text)
    placeholders = [i for i in ids if i.upper() in _PLACEHOLDERS]
    invalid = [i for i in ids if i not in placeholders and i not in retrieved.ids]
    cited = [i for i in ids if i in retrieved.ids]

    # A block with NOTHING grounded around it is the model declining. A block inside an answer
    # that cites valid sources is a section gap (e.g. no jurisprudence retrieved): the answer
    # stands and the UI shows the system's own "no sources for this section" text.
    refused = gaps > 0 and not cited
    folded = fold(text)
    prose = (not refused) and (not cited) and any(m in folded for m in _PROSE_REFUSAL)

    uncovered: list[str] = []
    bad_quotes: list[str] = []
    quote_fixes: list[str] = []
    decree_bad: list[str] = []
    share = 0.0
    if not refused and not prose:
        uncovered, share = _coverage(
            text, retrieved, workflow in STRATEGIC_WORKFLOWS, cfg.coverage_min_words
        )
        with_fixes = _bad_quotes_with_fixes(text, retrieved, cfg.quote_min_words)
        bad_quotes = [b for b, _ in with_fixes]
        quote_fixes = [f for _, f in with_fixes]
        decree_bad = _decree_mismatches(text, retrieved)

    grounded = (
        not refused
        and not prose
        and bool(cited)
        and not invalid
        and not placeholders
        and not uncovered
        and not bad_quotes
        and not decree_bad
    )
    return Verdict(
        cited=tuple(cited),
        invalid=tuple(invalid),
        placeholders=tuple(placeholders),
        refused=refused,
        gaps=gaps,
        prose_refusal=prose,
        uncovered=tuple(uncovered),
        bad_quotes=tuple(bad_quotes),
        quote_fixes=tuple(quote_fixes),
        decree_mismatches=tuple(decree_bad),
        analysis_share=round(share, 4),
        grounded=grounded,
    )

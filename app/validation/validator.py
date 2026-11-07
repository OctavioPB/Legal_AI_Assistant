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
_UNIT_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[)")
_ELLIPSIS = re.compile(r"\.\.\.|…")

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


def _line_units(line: str) -> list[str]:
    pieces = [
        "".join(_UNMASK.get(ch, ch) for ch in u).strip()
        for u in _UNIT_SPLIT.split(_mask_quotes(line.strip()))
        if u.strip()
    ]
    merged: list[str] = []
    for piece in pieces:
        if merged and _ABBREV_END.search(merged[-1]) and _NUMBER_START.match(piece):
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


def _coverage(
    answer: str, retrieved: RetrievedSet, strategic_ok: bool, min_words: int
) -> tuple[list[str], float]:
    """Every legal-looking sentence needs a cite or an allowed tag.

    A tag ALONE on its line labels the paragraph that follows (seen with the real local model:
    "[análisis propio]" on its own line, then the paragraph), until a blank line or a new heading.
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
        for unit in _line_units(line):
            body = _plain(unit)
            if len(body.split()) < min_words or body.endswith(":"):
                label = None  # a heading/label line starts a new section
                continue
            total += 1
            if any(cid in unit for cid in retrieved.ids):
                continue  # a source listing names its fragment
            has_cite = bool(_CITE.search(unit))
            analysis = OWN_ANALYSIS_TAG in unit or label == "own"
            strategic = strategic_ok and (STRATEGIC_TAG in unit or label == "strategic")
            if analysis or strategic:
                tagged += 1
            if not (has_cite or analysis or strategic):
                uncovered.append(body[:140])
    return uncovered, (tagged / total if total else 0.0)


def _bad_quotes(answer: str, retrieved: RetrievedSet, min_words: int) -> list[str]:
    bad: list[str] = []
    for unit in _units(answer):
        quotes = [unit[s + 1 : e - 1] for s, e in _quote_spans(unit)]
        if not quotes:
            continue
        allowed = [
            _fold_cached(t)
            for c in parse_citations(unit)
            if c in retrieved.ids
            for t in retrieved.texts[c]
        ]
        for q in quotes:
            for seg in _ELLIPSIS.split(q):
                seg_f = fold_words(seg).strip(" .,;:")
                if len(seg_f.split()) >= min_words and not any(seg_f in a for a in allowed):
                    bad.append(seg.strip()[:140])
    return bad


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
    share = 0.0
    if not refused and not prose:
        uncovered, share = _coverage(
            text, retrieved, workflow == "adversarial", cfg.coverage_min_words
        )
        bad_quotes = _bad_quotes(text, retrieved, cfg.quote_min_words)

    grounded = (
        not refused
        and not prose
        and bool(cited)
        and not invalid
        and not placeholders
        and not uncovered
        and not bad_quotes
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
        analysis_share=round(share, 4),
        grounded=grounded,
    )

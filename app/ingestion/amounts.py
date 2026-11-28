"""ADR-038 (D): a sum written twice must say the same thing twice.

Mexican contracts write amounts as a figure and in words:
"$15,000.00 (QUINCE MIL PESOS 00/100 M.N.)".
OCR can misread a digit on a page read above the confidence threshold (ADR-030), and the verbatim
check would only compare a quotation with the misreading. Code compares the two spellings instead:
a mismatch is reported on the text, never silently fixed (which one is wrong is a person's call).
Deterministic, no model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400,
    "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600,
    "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}  # fmt: skip
_STOP = {"pesos", "peso", "dolares", "dolar", "m.n", "mn", "moneda", "nacional", "y"}


def _fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()


def words_to_int(words: str) -> int | None:
    """'QUINCE MIL' -> 15000; 'UN MILLÓN DOSCIENTOS MIL' -> 1200000; None if a word is unknown."""
    total = current = 0
    seen = False
    for w in re.findall(r"[a-zñ.]+", _fold(words)):
        w = w.strip(".")
        if w in ("y", ""):
            continue
        if w in _UNITS:
            current += _UNITS[w]
            seen = True
        elif w == "mil":
            current = (current or 1) * 1_000
            seen = True
        elif w in ("millon", "millones"):
            total += (current or 1) * 1_000_000
            current = 0
            seen = True
        elif w in _STOP:
            break
        else:
            return None
    if not seen:
        return None
    # "mil" multiplies only what came before it inside the current millions group
    return total + current


_PAIR = re.compile(
    r"\$\s?(?P<fig>\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)\s*"
    r"\((?P<words>[^)(]{3,160}?)\s+(?P<cents>\d{2})\s*/\s*100[^)]*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Mismatch:
    text: str  # the sum as written ("$15,000.00 (QUINCE MIL PESOS 00/100 M.N.)")
    figure: int  # the whole pesos of the figure
    in_words: int  # the whole pesos the words say


def _figure_pesos(fig: str) -> int:
    digits = re.sub(r"[^\d]", "", fig)
    # a trailing ".dd" / ",dd" is cents: drop the last two digits when the figure ends with them
    if re.search(r"[.,]\d{2}$", fig):
        digits = digits[:-2]
    return int(digits or "0")


def mismatches(text: str) -> list[Mismatch]:
    """Every '$figure (words NN/100 ...)' whose figure and words give different whole amounts."""
    out: list[Mismatch] = []
    for m in _PAIR.finditer(text):
        words = re.split(r"\b(?:pesos?|d[oó]lares?)\b", m["words"], flags=re.IGNORECASE)[0]
        n = words_to_int(words)
        if n is None:
            continue  # words we cannot read are not evidence of anything
        fig = _figure_pesos(m["fig"])
        if fig != n:
            out.append(Mismatch(m.group(0), fig, n))
    return out

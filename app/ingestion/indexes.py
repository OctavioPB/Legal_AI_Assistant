"""Official indexes of laws (ADR-026): what exists, so a person can decide what to load.

An index is a page published by the authority that lists the laws in force with their official
download. It is read like any watched source (closed list below, https, robots.txt, one request,
conditional GET) and parsed by deterministic code. Reading an index downloads NO law: its entries
land in `corpus_index_entries` and wait for an operator's decision (`app/ingestion/discover.py`).

A parser that suddenly finds far fewer entries than usual means the page changed shape: the read
fails loudly (`IndexLayoutError`) instead of reporting hundreds of laws as gone.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Literal

_DATE = r"(\d{2})/(\d{2})/(\d{4})"
_DOF = re.compile(r"DOF\s+" + _DATE)


@dataclass(frozen=True)
class IndexEntry:
    key: str  # the official file name without extension (stable id of the law in that index)
    title: str
    url: str
    published_on: date | None
    last_reform_on: date | None
    last_reform_text: str  # e.g. "DOF 14/05/2026", "Sin reforma", "DOF 16/01/2026 Sentencia SCJN"
    note: str  # what the index says next to the title ("Nueva reforma", "(Abrogado ...)")


class IndexLayoutError(ValueError):
    """The page no longer has the shape the parser knows: nothing is updated."""


@dataclass(frozen=True)
class IndexSpec:
    index_id: str
    title: str
    url: str
    parse: Callable[[bytes, str], list[IndexEntry]]
    min_entries: int  # fewer means the layout changed (see IndexLayoutError)
    # Everything an approved entry is filed under comes from here (closed), never from the page.
    jurisdiction: tuple[str, str, str | None, str | None]
    scope: str
    language: Literal["es", "en"]
    grammar: Literal["mx", "us"]


class TableRows(HTMLParser):
    """Table rows as lists of cells (text, hrefs)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, list[str]]]] = []
        self._row: list[tuple[str, list[str]]] | None = None
        self._text: list[str] = []
        self._hrefs: list[str] = []
        self._in_cell = False

    def _close_cell(self) -> None:
        if self._in_cell and self._row is not None:
            self._row.append((" ".join("".join(self._text).split()), self._hrefs))
        self._in_cell, self._text, self._hrefs = False, [], []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._close_cell()
            self._row = []
        elif tag == "td" and self._row is not None:
            self._close_cell()
            self._in_cell = True
        elif tag == "a" and self._in_cell:
            href = dict(attrs).get("href")
            if href:
                self._hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._close_cell()
        elif tag == "tr" and self._row is not None:
            self._close_cell()
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text.append(data)


def decode_html(body: bytes) -> str:
    """UTF-8 when it is valid, else Windows-1252 (the Diputados pages are ISO-8859-1)."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def _date(m: re.Match[str] | None) -> date | None:
    if m is None:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


_PDF = re.compile(r"^pdf/([A-Za-z0-9][A-Za-z0-9_.-]{0,80})\.pdf$")


def parse_diputados(body: bytes, base_url: str) -> list[IndexEntry]:
    """Cámara de Diputados, "Leyes Federales Vigentes": one row per law with its number, its title
    followed by "DOF dd/mm/yyyy" (publication) and an optional note, the last reform ("DOF
    dd/mm/yyyy", "Sin reforma", "... Sentencia SCJN") and the links (pdf/KEY.pdf)."""
    p = TableRows()
    p.feed(decode_html(body))
    out: dict[str, IndexEntry] = {}
    for row in p.rows:
        if len(row) != 4 or not row[0][0].isdigit():
            continue
        pdfs = [m.group(1) for href in row[3][1] if (m := _PDF.match(href))]
        if not pdfs:
            continue
        cell = row[1][0]
        m = _DOF.search(cell)
        title = (cell[: m.start()] if m else cell).strip()
        note = cell[m.end() :].strip() if m else ""
        if not title:
            continue
        key = pdfs[0]
        out.setdefault(
            key,
            IndexEntry(
                key=key,
                title=title,
                url=urllib.parse.urljoin(base_url, f"pdf/{key}.pdf"),
                published_on=_date(m),
                last_reform_on=_date(_DOF.search(row[2][0])),
                last_reform_text=row[2][0][:200],
                note=note[:300],
            ),
        )
    return list(out.values())


INDEXES: dict[str, IndexSpec] = {
    "DIP_FED": IndexSpec(
        index_id="DIP_FED",
        title="Cámara de Diputados — Leyes Federales Vigentes",
        url="https://www.diputados.gob.mx/LeyesBiblio/index.htm",
        parse=parse_diputados,
        min_entries=200,  # 320 entries on 2026-11-17
        jurisdiction=("MX", "federal", None, None),
        scope="FED",
        language="es",
        grammar="mx",
    ),
}


def read_index(spec: IndexSpec, body: bytes) -> list[IndexEntry]:
    entries = spec.parse(body, spec.url)
    if len(entries) < spec.min_entries:
        raise IndexLayoutError(
            f"{spec.index_id}: {len(entries)} entries (< {spec.min_entries}): "
            "the page changed shape"
        )
    return entries


__all__ = [
    "INDEXES",
    "IndexEntry",
    "IndexLayoutError",
    "IndexSpec",
    "parse_diputados",
    "read_index",
]

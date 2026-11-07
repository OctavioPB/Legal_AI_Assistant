"""PDF -> plain text (WS2.T02). Docling owns layout and reading order (ADR-004); the legal
semantics live in the normalizer. This module only converts and cleans Docling's typography.

`PdfParser` is a Protocol so tests and the worker never import Docling concretely; the Docling
import is lazy (it pulls torch and downloads layout models on first use).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ParsedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    raw_text: str = ""  # Docling's export before cleanup: cleanup rules iterate on this
    page_count: int
    warnings: tuple[str, ...] = ()
    parser: str


class PdfParser(Protocol):
    name: str

    def parse(self, path: Path) -> ParsedDocument: ...


# Docling renders list items as markdown bullets ("- I. Texto", "- a) Texto"). In a statute those
# are fracciones/incisos, not bullets: drop the marker so the grammar sees the legal numbering.
_BULLET = re.compile(r"^[ \t]*[-*•][ \t]+(?=(?:[IVXLC]+[.)\-–—]|[a-zA-Z][.)]))", re.MULTILINE)
_PAGE_NUMBER = re.compile(r"^\s*(?:P[aá]gina\s+)?\d{1,4}\s*(?:de\s+\d{1,4})?\s*$", re.I | re.M)


_SEPARATOR_ROW = re.compile(r"^\|?[\s:|-]+\|?$")
_BARE_MARKER = re.compile(r"^(?:[IVXLC]+\.|[a-z]\))$")
_STRUCTURE = re.compile(
    r"^(?:Art[ií]culo|LIBRO|T[IÍ]TULO|CAP[IÍ]TULO|SECCI[OÓ]N|TRANSITORIOS?|[IVXLC]+[.)]|[a-z]\))",
    re.IGNORECASE,
)
MIN_HEADER_LEN = 25  # shorter repeated lines ("Se deroga.", "TRANSITORIOS") are legal text
# Repeated lines that are CONTENT, not page furniture: reform/vigencia annotations and list items.
# "Última Reforma DOF ..." is the exception: it is the running header of the diputados PDFs.
_CONTENT_LINE = re.compile(
    r"^(?!.*Última\s+Reforma)(?:[-*•]\s|.*(?:DOF|P\.\s?O\.|reformad|adicionad|derogad|"
    r"fe\s+de\s+erratas))",
    re.IGNORECASE,
)


def _table_to_lines(line: str) -> list[str]:
    """Docling renders runs of short rows (typically derogated articles with side notes) as
    markdown tables. Statute text is not tabular: emit one line per distinct cell, joining a bare
    fracción marker with its text ("I." + "Se deroga." -> "I. Se deroga.")."""
    if _SEPARATOR_ROW.match(line):
        return []
    cells: list[str] = []
    for cell in (c.strip() for c in line.strip().strip("|").split("|")):
        if cell and (not cells or cell != cells[-1]):
            cells.append(cell)
    if len(cells) >= 2 and _BARE_MARKER.match(cells[0]):
        cells = [f"{cells[0]} {cells[1]}", *cells[2:]]
    return cells


def strip_running_headers(text: str, *, pages: int | None = None) -> tuple[str, list[str]]:
    """Drop lines that repeat on (almost) every page: running headers/footers. Returns the text
    and the distinct lines removed so the caller can log them (never silent)."""
    lines = text.split("\n")
    counts: dict[str, int] = {}
    for ln in lines:
        key = ln.strip()
        if (
            len(key) >= MIN_HEADER_LEN
            and not _STRUCTURE.match(key)
            and not _CONTENT_LINE.match(key)
            and not key.endswith((".", ":", ";", ","))  # sentences are content, furniture is not
        ):
            counts[key] = counts.get(key, 0) + 1
    threshold = max(10, (pages or 0) // 10)
    noise = {k for k, v in counts.items() if v >= threshold}
    kept = [ln for ln in lines if ln.strip() not in noise]
    return "\n".join(kept), sorted(noise)


def clean_docling_text(raw: str, *, pages: int | None = None) -> str:
    return clean_docling_text_with_report(raw, pages=pages)[0]


def clean_docling_text_with_report(raw: str, *, pages: int | None = None) -> tuple[str, list[str]]:
    out: list[str] = []
    for line in raw.split("\n"):
        if line.lstrip().startswith("|"):
            out.extend(_table_to_lines(line))
        else:
            out.append(line)
    text = _BULLET.sub("", "\n".join(out))
    text = _PAGE_NUMBER.sub("", text)  # isolated page numbers would become loose "párrafos"
    return strip_running_headers(text, pages=pages)


class DoclingParser:
    name = "docling"

    def __init__(self, *, ocr: bool = False) -> None:
        self._ocr = ocr  # official PDFs have a text layer; OCR only for scans
        self._converter: object | None = None

    def _get(self) -> object:
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions(do_ocr=self._ocr, do_table_structure=True)
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return self._converter

    def parse(self, path: Path, *, page_range: tuple[int, int] | None = None) -> ParsedDocument:
        conv = self._get()
        kwargs = {"page_range": page_range} if page_range else {}
        result = conv.convert(path, **kwargs)  # type: ignore[attr-defined]
        doc = result.document
        pages = len(doc.pages)
        raw = doc.export_to_text()
        text, noise = clean_docling_text_with_report(raw, pages=pages)
        warnings: list[str] = []
        if noise:
            warnings.append(f"removed {len(noise)} running header/footer line(s): {noise[:3]}")
        blank = [n for n, p in doc.pages.items() if not _page_has_text(doc, n)]
        if blank:
            warnings.append(f"pages without text layer (OCR needed?): {sorted(blank)[:20]}")
        if len(text.strip()) < 50 * max(pages, 1):
            warnings.append("very little text extracted for the page count")
        return ParsedDocument(
            text=text,
            raw_text=raw,
            page_count=pages,
            warnings=tuple(warnings),
            parser=self.name,
        )


def _page_has_text(doc: object, page_no: int) -> bool:
    return any(
        any(p.page_no == page_no for p in item.prov)
        for item in doc.texts  # type: ignore[attr-defined]
    )

"""A firm's PDF -> text, with OCR for the pages that need it (ADR-025, ADR-030).

Page by page, in order: runs of pages with a usable text layer go through Docling as before (the
same parser, the same clean-up); a page the gate flags (or every page, when the user asked to read
the PDF as scanned) is read by Tesseract. An OCR page whose mean word confidence is under
`OcrConfig.min_confidence` is NOT kept: its text never becomes citable, and the page number is
reported so the lawyer can rescan it. A misread figure in a clause must never be citable, and the
verbatim check would only compare a quotation with the misreading.

The PDF goes to disk once, as a temporary file for Docling, deleted in `finally` (as before).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.ingestion.ocr import OcrConfig, OcrMode, PageReader, needs_ocr, page_stats


class RangeParser(Protocol):
    def parse(self, path: Path, *, page_range: tuple[int, int] | None = None) -> Any: ...


class OcrUnavailableError(RuntimeError):
    """Pages need OCR and there is no Tesseract on this machine."""


class TooManyScannedPagesError(RuntimeError):
    """More scanned pages than `ocr_max_pages` (time bound: ~3-4 s per page on CPU)."""


@dataclass(frozen=True, slots=True)
class PdfText:
    text: str
    page_count: int
    ocr_pages: tuple[int, ...] = ()  # pages read by OCR and kept
    excluded_pages: tuple[int, ...] = ()  # OCR pages under the confidence threshold: left out
    ocr_confidence: float | None = None  # word-weighted mean over the kept OCR pages
    warnings: tuple[str, ...] = ()


def read_pdf(
    data: bytes,
    parser: RangeParser,
    *,
    mode: OcrMode,
    cfg: OcrConfig,
    reader: PageReader | None,
    max_ocr_pages: int,
) -> PdfText:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        stats = page_stats(pdf)
        flagged = {s.number for s in stats if needs_ocr(s, cfg, mode)}
        if flagged and reader is None:
            raise OcrUnavailableError(f"{len(flagged)} page(s) need OCR")
        if len(flagged) > max_ocr_pages:
            raise TooManyScannedPagesError(f"{len(flagged)} > {max_ocr_pages}")
        fd, name = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return _assemble(pdf, Path(name), stats, flagged, parser, reader, cfg)
        finally:
            Path(name).unlink(missing_ok=True)  # the original is never kept (ADR-025)
    finally:
        pdf.close()


def _assemble(
    pdf: Any, path: Path, stats: list[Any], flagged: set[int], parser: RangeParser,
    reader: PageReader | None, cfg: OcrConfig,
) -> PdfText:  # fmt: skip
    pages = len(stats)
    if not flagged:  # a digital PDF: exactly the previous path
        parsed = parser.parse(path)
        return PdfText(parsed.text, pages, warnings=tuple(parsed.warnings))
    parts: list[str] = []
    warnings: list[str] = []
    kept: list[int] = []
    excluded: list[int] = []
    weighted = 0.0
    words = 0
    n = 1
    while n <= pages:
        if n not in flagged:
            end = n
            while end + 1 <= pages and end + 1 not in flagged:
                end += 1
            parsed = parser.parse(path, page_range=(n, end))
            parts.append(parsed.text)
            warnings += list(parsed.warnings)
            n = end + 1
            continue
        assert reader is not None
        page = pdf[n - 1]
        try:
            result = reader(page, n)
        finally:
            page.close()
        if result.words and result.confidence >= cfg.min_confidence:
            parts.append(result.text)
            kept.append(n)
            weighted += result.confidence * result.words
            words += result.words
        elif result.words:
            excluded.append(n)
        n += 1  # a page OCR found nothing on (blank) is neither kept nor reported
    if excluded:
        warnings.append(f"OCR pages under the confidence threshold left out: {excluded}")
    return PdfText(
        "\n\n".join(p for p in parts if p.strip()), pages, tuple(kept), tuple(excluded),
        round(weighted / words, 3) if words else None, tuple(warnings),
    )  # fmt: skip

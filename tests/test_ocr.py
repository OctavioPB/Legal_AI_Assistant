"""OCR of scanned pages (ADR-030): the gate, the image clean-up, hOCR parsing, the page-by-page
assembly with fakes, and one real Tesseract read of a generated scan.

The real read needs Tesseract with Spanish and English data (README, docs/LOCAL_DEMO.md; CI installs
it). Like the database, a missing engine FAILS the test instead of skipping it: a skipped OCR test
would let a broken OCR path ship.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from app.ingestion.ocr import (
    OcrConfig,
    OcrEngine,
    OcrMode,
    OcrPageText,
    PageStats,
    estimate_skew,
    find_rule_boxes,
    needs_ocr,
    page_stats,
    parse_hocr,
    tesseract_reader,
)
from app.ingestion.pdf_read import (
    OcrUnavailableError,
    TooManyScannedPagesError,
    read_pdf,
)
from tests.pdf_fixtures import build_pdf, scan_image

CFG = OcrConfig()
CONTRACT = [
    "PRIMERA. El arrendador entrega el inmueble al arrendatario.",
    "SEGUNDA. La renta mensual es de quince mil pesos.",
    "TERCERA. El contrato termina el treinta de junio.",
]


# ── the gate ──────────────────────────────────────────────────────────────────────────────────────
def test_the_gate_flags_scans_and_garbage_text_layers_only() -> None:
    scan = PageStats(1, chars=0, garbage=0, image_cover=0.98)
    digital = PageStats(2, chars=2400, garbage=0, image_cover=0.0)
    blank = PageStats(3, chars=0, garbage=0, image_cover=0.0)  # nothing to read: not OCR'd
    broken = PageStats(4, chars=1000, garbage=600, image_cover=0.0)  # no ToUnicode map
    logo = PageStats(5, chars=5, garbage=0, image_cover=0.2)  # a small image: not a scan
    flagged = [s.number for s in (scan, digital, blank, broken, logo)
               if needs_ocr(s, CFG, OcrMode.AUTO)]  # fmt: skip
    assert flagged == [1, 4]
    assert all(needs_ocr(s, CFG, OcrMode.ALWAYS) for s in (digital, blank))
    assert not needs_ocr(scan, CFG, OcrMode.NEVER)


def test_page_stats_tell_a_text_page_from_a_scanned_one() -> None:
    pdf = pdfium.PdfDocument(build_pdf(["Una clausula con texto real.", scan_image(CONTRACT)]))
    try:
        text, scan = page_stats(pdf)
    finally:
        pdf.close()
    assert text.chars > 20 and text.image_cover == 0.0
    assert scan.chars == 0 and scan.image_cover > 0.95
    assert [needs_ocr(s, CFG, OcrMode.AUTO) for s in (text, scan)] == [False, True]


# ── image clean-up and hOCR ──────────────────────────────────────────────────────────────────────
def test_table_rules_are_found_and_text_strokes_are_not() -> None:
    img = Image.new("L", (800, 400), 255)
    draw = ImageDraw.Draw(img)
    draw.line((50, 100, 750, 100), fill=0, width=3)  # a table rule
    draw.line((400, 150, 400, 380), fill=0, width=3)  # a vertical rule
    draw.text((60, 200), "texto", fill=0)  # short strokes
    boxes = find_rule_boxes(img, min_length=150, max_thickness=10)
    assert {b[0] for b in boxes} == {True, False}
    assert all((b[3] - b[1] >= 150) or (b[4] - b[2] >= 150) for b in boxes)


def test_a_skewed_page_is_measured_within_a_step() -> None:
    page = scan_image(CONTRACT * 4, dpi=100)
    tilted = page.rotate(1.5, resample=Image.Resampling.BILINEAR, fillcolor=255)
    assert abs(estimate_skew(tilted, CFG) - 1.5) <= 0.3
    assert abs(estimate_skew(page, CFG)) <= 0.2


HOCR = b"""<html><body><div class='ocr_page'>
<p class='ocr_par'>
 <span class='ocr_line' title='bbox 10 10 500 40'>
  <span class='ocrx_word' title='bbox 10 10 80 40; x_wconf 96'>PRIMERA.</span>
  <span class='ocrx_word' title='bbox 90 10 160 40; x_wconf 91'>El</span>
  <span class='ocrx_word' title='bbox 170 10 300 40; x_wconf 88'>contra-</span>
 </span>
 <span class='ocr_line' title='bbox 10 50 500 80'>
  <span class='ocrx_word' title='bbox 10 50 60 80; x_wconf 90'>to</span>
  <span class='ocrx_word' title='bbox 70 50 80 80; x_wconf 20'>.,</span>
  <span class='ocrx_word' title='bbox 90 50 160 80; x_wconf 93'>obliga.</span>
 </span>
</p>
<p class='ocr_par'>
 <span class='ocr_line' title='bbox 10 120 500 150'>
  <span class='ocrx_word' title='bbox 10 120 90 150; x_wconf 95'>SEGUNDA.</span>
 </span>
</p></div></body></html>"""


def test_hocr_becomes_paragraphs_with_hyphenation_rejoined_and_speckles_dropped() -> None:
    text, confidence, words = parse_hocr(HOCR, CFG)
    assert text == "PRIMERA. El contrato obliga.\n\nSEGUNDA."
    assert words == 6  # the ".," speckle is not a word
    assert confidence == round((96 + 91 + 88 + 90 + 93 + 95) / 600, 3)


# ── page-by-page assembly (fakes) ───────────────────────────────────────────────────────────────
class FakeParser:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int] | None] = []
        self.paths: list[Path] = []

    def parse(self, path: Path, *, page_range: tuple[int, int] | None = None) -> Any:
        self.calls.append(page_range)
        self.paths.append(path)
        label = "ALL" if page_range is None else f"{page_range[0]}-{page_range[1]}"
        return SimpleNamespace(text=f"DIGITAL {label}", warnings=())


def fake_reader(confidence: dict[int, float]) -> Any:
    def read(page: Any, number: int) -> OcrPageText:
        return OcrPageText(number, f"OCR {number}", confidence.get(number, 0.9), words=10)

    return read


def test_a_digital_pdf_takes_exactly_the_previous_path() -> None:
    parser = FakeParser()
    out = read_pdf(build_pdf(["uno con texto suficiente", "dos con texto suficiente"]), parser,
                   mode=OcrMode.AUTO, cfg=CFG, reader=None, max_ocr_pages=10)  # fmt: skip
    assert parser.calls == [None]  # one Docling call over the whole file, no OCR, no engine needed
    assert (out.text, out.page_count, out.ocr_pages, out.excluded_pages) == (
        "DIGITAL ALL",
        2,
        (),
        (),
    )
    assert not parser.paths[0].exists()  # the temporary copy is gone (ADR-025)


def test_pages_stay_in_order_and_a_poor_scan_is_left_out_and_reported() -> None:
    parser = FakeParser()
    data = build_pdf([
        "primera pagina con texto real", scan_image(CONTRACT), "tercera pagina con texto",
        "cuarta pagina con texto", scan_image(CONTRACT),
    ])  # fmt: skip
    out = read_pdf(data, parser, mode=OcrMode.AUTO, cfg=CFG,
                   reader=fake_reader({5: 0.41}), max_ocr_pages=10)  # fmt: skip
    assert parser.calls == [(1, 1), (3, 4)]  # runs of digital pages, in order
    assert out.text == "DIGITAL 1-1\n\nOCR 2\n\nDIGITAL 3-4"  # page 5 never becomes citable
    assert (out.ocr_pages, out.excluded_pages, out.ocr_confidence) == ((2,), (5,), 0.9)
    assert any("left out" in w for w in out.warnings)


def test_reading_as_scanned_sends_every_page_to_ocr() -> None:
    parser = FakeParser()
    out = read_pdf(build_pdf(["texto con capa danada", "otra pagina"]), parser,
                   mode=OcrMode.ALWAYS, cfg=CFG, reader=fake_reader({}), max_ocr_pages=10)  # fmt: skip
    assert parser.calls == []
    assert (out.text, out.ocr_pages) == ("OCR 1\n\nOCR 2", (1, 2))


def test_no_engine_or_too_many_scanned_pages_fail_before_any_work() -> None:
    parser = FakeParser()
    scanned = build_pdf([scan_image(CONTRACT), scan_image(CONTRACT)])
    with pytest.raises(OcrUnavailableError):
        read_pdf(scanned, parser, mode=OcrMode.AUTO, cfg=CFG, reader=None, max_ocr_pages=10)
    with pytest.raises(TooManyScannedPagesError):
        read_pdf(scanned, parser, mode=OcrMode.AUTO, cfg=CFG, reader=fake_reader({}),
                 max_ocr_pages=1)  # fmt: skip
    assert parser.calls == []


# ── the real engine ──────────────────────────────────────────────────────────────────────────────
def real_engine() -> OcrEngine:
    engine = OcrEngine.find(CFG)
    assert engine is not None, "Tesseract is required for the OCR tests (docs/LOCAL_DEMO.md)"
    return engine


def test_tesseract_reads_a_scanned_contract_page() -> None:
    engine = real_engine()
    parser = FakeParser()
    out = read_pdf(build_pdf([scan_image(CONTRACT)]), parser, mode=OcrMode.AUTO, cfg=CFG,
                   reader=tesseract_reader(engine, CFG), max_ocr_pages=10)  # fmt: skip
    assert out.ocr_pages == (1,) and out.excluded_pages == ()
    assert out.ocr_confidence is not None and out.ocr_confidence >= CFG.min_confidence
    words = out.text.lower()
    for expected in ("primera", "arrendador", "inmueble", "segunda", "renta", "tercera"):
        assert expected in words, out.text
    assert engine.version()  # recorded as provenance with the upload


def test_tesseract_upright_a_page_scanned_upside_down() -> None:
    engine = real_engine()
    upside_down = scan_image(CONTRACT * 3).rotate(180)
    out = read_pdf(build_pdf([upside_down]), FakeParser(), mode=OcrMode.AUTO, cfg=CFG,
                   reader=tesseract_reader(engine, CFG), max_ocr_pages=10)  # fmt: skip
    assert "arrendador" in out.text.lower(), out.text

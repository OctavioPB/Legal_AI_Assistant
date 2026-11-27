"""OCR for scanned PDF pages (ADR-030): the per-page gate and Tesseract via pytesseract.

Adapted from Markdown-Conversor (tomd), `src/tomd/readers/pdf/ocr.py` and `extract.py` at commit
4ad49eb (same author; its ADR-017). Kept: the gate (no text layer under a page-sized image, or a
text layer of garbage characters), rendering at 300 dpi within a pixel budget, contrast stretch,
orientation by OSD, deskew by projection profile, erasing table rules before recognition, hOCR
parsing with per-word confidence and speckle removal. Dropped: font size, bold, bullets and table
grids, which tomd needs to write Markdown and this project does not (it needs paragraphs of text).
The numeric constants are tomd's, measured on its scan corpus; each comment says why.

Privilege (ADR-025): the page is rendered in memory. pytesseract hands the image to the Tesseract
executable through a temporary file in the OS temp directory and deletes it when the call returns;
nothing else of the document touches the disk.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw, ImageOps


class OcrMode(Enum):
    AUTO = "auto"  # only the pages the gate flags
    ALWAYS = "always"  # every page: a legible but wrong text layer ("read as scanned")
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class OcrConfig:
    languages: str = "spa+eng"
    dpi: int = 300  # Tesseract's recommended minimum for body text
    # Pages with fewer extracted characters than this have no usable text layer...
    min_text_chars: int = 20
    # ... and are a scan when an image covers more than this share of the page.
    min_image_cover: float = 0.5
    # A text layer with more than this share of replacement/private-use/control characters
    # (a missing ToUnicode map) is unreadable and needs OCR too.
    garbage_ratio: float = 0.3
    # Mean word confidence below which a page is not trusted (tomd's pdf.ocr_low_confidence).
    min_confidence: float = 0.6
    # Pixel budget of one rendered page: 40 MP is A2 at 300 dpi (Tesseract then needs ~1 GB).
    max_pixels: int = 40_000_000
    # Share (percent) of darkest/lightest pixels ignored when stretching contrast.
    contrast_cutoff: float = 0.1
    # OSD answers on upright text pages score 10-20; sparse pages score below 2.
    orientation_confidence: float = 2.0
    # Skew search range and step (degrees): hand-fed scans are rarely off by more than 3°.
    max_skew: float = 3.0
    skew_step: float = 0.1
    min_skew: float = 0.2  # below this, deskewing is not worth the resampling blur
    # Straight ink runs at least this long (points) and at most this thick are table rules.
    rule_min_length: float = 36.0
    rule_max_thickness: float = 2.5
    # Punctuation-only "words" under this confidence are noise speckles.
    speckle_confidence: float = 0.6
    tesseract_cmd: str | None = None
    tessdata: str | None = None

    @classmethod
    def from_settings(cls, s: Any) -> OcrConfig:
        return cls(
            languages=s.ocr_languages, dpi=s.ocr_dpi, min_confidence=s.ocr_min_confidence,
            tesseract_cmd=s.ocr_tesseract_cmd, tessdata=s.ocr_tessdata,
        )  # fmt: skip


# ── the gate ──────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class PageStats:
    number: int  # 1-based
    chars: int
    garbage: int
    image_cover: float  # largest image's share of the page area


def is_garbage(ch: str) -> bool:
    """Replacement, private-use and control characters: a missing ToUnicode map."""
    if ch == "\ufffd":
        return True
    code = ord(ch)
    if 0xE000 <= code <= 0xF8FF:
        return True
    return unicodedata.category(ch) == "Cc" and ch not in "\t\n\r"


def needs_ocr(stats: PageStats, cfg: OcrConfig, mode: OcrMode) -> bool:
    if mode is OcrMode.NEVER:
        return False
    if mode is OcrMode.ALWAYS:
        return True
    if stats.chars < cfg.min_text_chars and stats.image_cover > cfg.min_image_cover:
        return True
    return bool(stats.chars) and stats.garbage / stats.chars > cfg.garbage_ratio


_Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_MAX_FORM_DEPTH = 8


def page_stats(pdf: Any) -> list[PageStats]:
    """Characters, garbage characters and image cover of every page of a pypdfium2 document."""
    import pypdfium2.raw as pdfium_c

    out: list[PageStats] = []
    for index in range(len(pdf)):
        page = pdf[index]
        try:
            width, height = page.get_size()
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
            visible = [ch for ch in text if not ch.isspace()]
            area = width * height or 1.0
            cover = 0.0
            forms: dict[int, _Matrix] = {}
            for obj in page.get_objects(max_depth=_MAX_FORM_DEPTH):
                level = obj.level
                parent = forms.get(level - 1, _IDENTITY) if level else _IDENTITY
                if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
                    forms[level] = _compose(tuple(obj.get_matrix().get()), parent)
                    for deeper in [k for k in forms if k > level]:
                        del forms[deeper]
                if obj.type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    continue
                x0, y0, x1, y1 = _transform(obj.get_bounds(), parent) if level else obj.get_bounds()
                cover = max(cover, abs(x1 - x0) * abs(y1 - y0) / area)
            out.append(
                PageStats(index + 1, len(visible), sum(map(is_garbage, visible)), min(cover, 1.0))
            )
        finally:
            page.close()
    return out


def _compose(inner: tuple[float, ...], outer: _Matrix) -> _Matrix:
    a, b, c, d, e, f = inner
    A, B, C, D, E, F = outer
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E,
            e * B + f * D + F)  # fmt: skip


def _transform(bounds: tuple[float, float, float, float], m: _Matrix) -> tuple[float, ...]:
    x0, y0, x1, y1 = bounds
    pts = [(x, y) for x in (x0, x1) for y in (y0, y1)]
    xs = [m[0] * x + m[2] * y + m[4] for x, y in pts]
    ys = [m[1] * x + m[3] * y + m[5] for x, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ── the engine ────────────────────────────────────────────────────────────────────────────────────
_WINDOWS_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")


class OcrFailedError(RuntimeError):
    """Tesseract could not run (missing language data, crashed engine)."""


@dataclass(frozen=True, slots=True)
class OcrEngine:
    command: str
    tessdata: str | None
    languages: str

    @classmethod
    def find(cls, cfg: OcrConfig) -> OcrEngine | None:
        """The configured executable, else PATH, else the default Windows install. None: there is
        no OCR on this machine (the upload then fails with `ocr_unavailable`)."""
        command = cfg.tesseract_cmd or shutil.which("tesseract")
        if not command and _WINDOWS_TESSERACT.is_file():
            command = str(_WINDOWS_TESSERACT)
        if not command:
            return None
        return cls(command, cfg.tessdata or _tessdata_dir(), cfg.languages)

    def version(self) -> str:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = self.command
        return str(pytesseract.get_tesseract_version())

    def missing_languages(self) -> list[str]:
        """Languages of `self.languages` with no traineddata where this engine looks."""
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = self.command
        with _tesseract_env(self):
            have = set(pytesseract.get_languages(config=""))
        return [lang for lang in self.languages.split("+") if lang not in have]


def _tessdata_dir() -> str | None:
    """Language data next to tomd's (same machine, same files) or in this project's own folder;
    otherwise Tesseract's installation decides."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "legalai" / "tessdata",
        Path(os.environ.get("LOCALAPPDATA", "")) / "tomd" / "tessdata",
        Path.home() / ".local" / "share" / "legalai" / "tessdata",
    ]
    return next((str(c) for c in candidates if c.is_dir() and any(c.glob("*.traineddata"))), None)


@contextmanager
def _tesseract_env(engine: OcrEngine) -> Iterator[None]:
    # The data folder goes through the environment: pytesseract splits `config` with Windows
    # shell rules, which keep quotes around paths with spaces.
    saved = {key: os.environ.get(key) for key in ("TESSDATA_PREFIX", "OMP_THREAD_LIMIT")}
    if engine.tessdata:
        os.environ["TESSDATA_PREFIX"] = engine.tessdata
    os.environ["OMP_THREAD_LIMIT"] = "1"  # deterministic, bounded memory
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass(frozen=True, slots=True)
class OcrPageText:
    number: int
    text: str  # paragraphs separated by a blank line
    confidence: float  # mean word confidence, 0-1 (0.0 when no word was read)
    words: int
    quarter_turns: int = 0
    skew: float = 0.0
    dpi: int = 300


class PageReader(Protocol):
    """Reads one pypdfium2 page; injected so tests never need Tesseract for the pipeline logic."""

    def __call__(self, page: Any, number: int) -> OcrPageText: ...


def tesseract_reader(engine: OcrEngine, cfg: OcrConfig) -> PageReader:
    def read(page: Any, number: int) -> OcrPageText:
        return ocr_page(engine, page, number, cfg)

    return read


def ocr_page(engine: OcrEngine, page: Any, number: int, cfg: OcrConfig) -> OcrPageText:
    """Render one page, make it upright, level and contrasted, and read its paragraphs."""
    import pytesseract  # lazily: only scanned documents need it

    pytesseract.pytesseract.tesseract_cmd = engine.command
    width_pt, height_pt = page.get_size()
    dpi = min(float(cfg.dpi), 72 * math.sqrt(cfg.max_pixels / max(width_pt * height_pt, 1.0)))
    image = page.render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
    angle = 0.0
    turns = 0
    try:
        with _tesseract_env(engine):
            # Faded ink on grey paper: stretch to the full range (a clean scan is unchanged).
            image = _swap(image, ImageOps.autocontrast(image, cutoff=cfg.contrast_cutoff))
            turns = _quarter_turns(pytesseract, image, cfg)
            if turns:
                image = _swap(image, image.rotate(-90 * turns, expand=True))
            angle = estimate_skew(image, cfg)
            if abs(angle) >= cfg.min_skew:
                level = image.rotate(-angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
                image = _swap(image, level)
            # Table rules confuse Tesseract's layout analysis (rows of a grid go missing).
            px = dpi / 72
            rules = find_rule_boxes(image, cfg.rule_min_length * px, cfg.rule_max_thickness * px)
            if rules:
                image = _swap(image, _erase(image, rules))
            hocr = pytesseract.image_to_pdf_or_hocr(
                image, lang=engine.languages, config="--psm 3", extension="hocr"
            )
    except (pytesseract.TesseractError, OSError) as exc:
        raise OcrFailedError(str(exc).strip()) from exc
    finally:
        image.close()
    text, confidence, words = parse_hocr(hocr, cfg)
    skew = angle if abs(angle) >= cfg.min_skew else 0.0
    return OcrPageText(number, text, confidence, words, turns, skew, round(dpi))


# ── image clean-up (verbatim from tomd, minus the rulings it keeps for table grids) ──────────────
_DARK_LEVEL = 128  # grey level below which a pixel counts as ink (after contrast stretch)
_INK = bytes([255])
_SKEW_WIDTH = 600  # thumbnail width used to estimate skew (enough to resolve text rows)
RuleBox = tuple[bool, int, int, int, int]  # horizontal, x0, y0, x1, y1 (pixels, end exclusive)


def find_rule_boxes(image: Image.Image, min_length: float, max_thickness: float) -> list[RuleBox]:
    """Straight ink runs at least `min_length` pixels long and at most `max_thickness` thick:
    table rules and separators. Text strokes are far shorter; filled areas are thicker."""
    ink = image.point(lambda v: 255 if v < _DARK_LEVEL else 0)
    boxes = _row_runs(ink, int(min_length), max_thickness)
    turned = ink.transpose(Image.Transpose.TRANSPOSE)  # columns become rows
    boxes += [
        (False, y0, x0, y1, x1)
        for _, x0, y0, x1, y1 in _row_runs(turned, int(min_length), max_thickness)
    ]
    ink.close()
    turned.close()
    return boxes


def _row_runs(ink: Image.Image, min_length: int, max_thickness: float) -> list[RuleBox]:
    pattern = re.compile(re.escape(_INK) + b"{%d,}" % max(min_length, 1))
    data, width = ink.tobytes(), ink.width
    open_boxes: list[list[int]] = []  # x0, y0, x1, y1 of runs still growing downwards
    done: list[list[int]] = []
    for y in range(ink.height):
        row = data[y * width : (y + 1) * width]
        grown: list[list[int]] = []
        for m in pattern.finditer(row):
            for box in open_boxes:
                if box[3] == y and m.start() < box[2] and box[0] < m.end():
                    box[0], box[2], box[3] = min(box[0], m.start()), max(box[2], m.end()), y + 1
                    grown.append(box)
                    break
            else:
                grown.append([m.start(), y, m.end(), y + 1])
        done += [box for box in open_boxes if box[3] != y + 1]
        open_boxes = list({id(box): box for box in grown}.values())
    done += open_boxes
    return [
        (True, x0, y0, x1, y1)
        for x0, y0, x1, y1 in sorted(done, key=lambda b: (b[1], b[0]))
        if y1 - y0 <= max_thickness
    ]


def _erase(image: Image.Image, boxes: list[RuleBox]) -> Image.Image:
    clean = image.copy()
    draw = ImageDraw.Draw(clean)
    for _, x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 - 1, y0 - 1, x1, y1), fill=255)  # one pixel of anti-aliasing
    return clean


def _swap(old: Image.Image, new: Image.Image) -> Image.Image:
    if new is not old:
        old.close()
    return new


def _quarter_turns(pytesseract: Any, image: Image.Image, cfg: OcrConfig) -> int:
    """Clockwise quarter turns that make the page upright, from Tesseract's OSD. Pages too sparse
    for OSD, or a low-confidence answer, are left as they are."""
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0", output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0
    if float(osd.get("orientation_conf", 0.0)) < cfg.orientation_confidence:
        return 0
    return int(osd.get("rotate", 0)) // 90 % 4


def estimate_skew(image: Image.Image, cfg: OcrConfig) -> float:
    """Skew in degrees (counter-clockwise) by projection profile: rows of text are sharpest, so
    row darkness varies most, when the page is level."""
    small = image.resize(
        (_SKEW_WIDTH, max(1, image.height * _SKEW_WIDTH // image.width)), Image.Resampling.BOX
    )
    steps = round(cfg.max_skew / cfg.skew_step)
    best, best_score = 0.0, -1.0
    for k in range(-steps, steps + 1):
        angle = k * cfg.skew_step
        level = small.rotate(-angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        rows = level.resize((1, level.height), Image.Resampling.BOX).tobytes()
        mean = sum(rows) / len(rows)
        score = sum((r - mean) ** 2 for r in rows)
        # Ties keep the angle closest to zero (k runs from negative to positive).
        if score > best_score + 1e-9 and (score > best_score or abs(angle) < abs(best)):
            best, best_score = angle, score
        level.close()
    small.close()
    return round(best, 2)


# ── hOCR -> paragraphs ────────────────────────────────────────────────────────────────────────────
_LINE_CLASSES = frozenset({"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"})
_BBOX = re.compile(r"bbox (-?\d+) (-?\d+) (-?\d+) (-?\d+)")
_WCONF = re.compile(r"x_wconf (\d+)")
_HYPHEN_END = re.compile(r"(\w)[-\u00ad]$")


def parse_hocr(hocr: bytes, cfg: OcrConfig) -> tuple[str, float, int]:
    """Tesseract hOCR to (text, mean word confidence, word count). Tesseract's paragraphs become
    paragraphs (a blank line between them); its lines are joined, and a word hyphenated at a line
    end is rejoined only when the next line starts in lower case ("contra-" + "to")."""
    from lxml import html

    root = html.fromstring(hocr)
    paragraphs: list[str] = []
    confidences: list[float] = []
    for paragraph in root.iter("p"):
        lines: list[str] = []
        for element in paragraph.iter("span"):
            if element.get("class") not in _LINE_CLASSES:
                continue
            words = [w for w in _words(element) if not _is_speckle(w[0], w[1], cfg)]
            if not words:
                continue
            confidences += [conf for _, conf in words]
            lines.append(" ".join(w for w, _ in words))
        if lines:
            paragraphs.append(_join_lines(lines))
    mean = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    return "\n\n".join(paragraphs), mean, len(confidences)


def _join_lines(lines: list[str]) -> str:
    out = lines[0]
    for nxt in lines[1:]:
        joined = _HYPHEN_END.search(out) and nxt[:1].islower()
        out = out[:-1] + nxt if joined else f"{out} {nxt}"
    return out


def _words(line: Any) -> list[tuple[str, float]]:
    words: list[tuple[int, str, float]] = []
    for element in line.iter("span"):
        if element.get("class") != "ocrx_word":
            continue
        word = element.text_content().strip()
        title = element.get("title", "")
        box = _BBOX.search(title)
        conf = _WCONF.search(title)
        if not word or box is None:
            continue
        words.append((int(box.group(1)), word, int(conf.group(1)) / 100 if conf else 0.0))
    return [(w, c) for _, w, c in sorted(words, key=lambda t: t[0])]


def _is_speckle(text: str, conf: float, cfg: OcrConfig) -> bool:
    """Noise read as a lone punctuation "word" (".", "-.", "'")."""
    return conf < cfg.speckle_confidence and not any(ch.isalnum() for ch in text)

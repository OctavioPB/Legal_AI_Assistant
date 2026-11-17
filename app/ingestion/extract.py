"""Bytes of an uploaded file -> plain text (ADR-025). Everything happens in memory; only a PDF
touches the disk, as a temporary file for the layout parser, deleted in `finally`.

Word files are read with the standard library (a .docx is a zip with `word/document.xml`): no
macro, link or embedded object is ever opened, only paragraph text. Uploads are untrusted input:
sizes are bounded before anything is expanded and XML with a DTD is refused.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Literal
from xml.etree import ElementTree

Format = Literal["pdf", "docx", "text"]

DOCX_XML_MAX_BYTES = 40 * 1024 * 1024  # uncompressed word/document.xml (zip-bomb bound)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_DECL = re.compile(r"^﻿?\s*<\?xml[^>]{0,200}\?>")


class UnsupportedFileError(ValueError):
    """Not a PDF, a .docx or plain text; or a file this reader refuses to expand."""


def detect_format(data: bytes) -> Format:
    """By content, never by the name or the declared type."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        raise UnsupportedFileError("a zip that is not a Word document")
    if b"\x00" in data[:4096]:
        raise UnsupportedFileError("binary file")
    return "text"


def docx_text(data: bytes) -> str:
    """Paragraph text of the document body, in order (tables: cell by cell). Headers, footers,
    footnotes and comments are not read."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > DOCX_XML_MAX_BYTES:
                raise UnsupportedFileError("document too large when expanded")
            with z.open(info) as f:
                xml = f.read(DOCX_XML_MAX_BYTES + 1)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnsupportedFileError("not a readable Word document") from exc
    if len(xml) > DOCX_XML_MAX_BYTES:
        raise UnsupportedFileError("refused: oversized XML")
    # The DTD check must look at exactly what the parser reads. Searching the raw bytes missed a
    # document declared in UTF-16, where "<!DOCTYPE" is not those bytes. Word writes UTF-8, so the
    # XML is decoded as strict UTF-8 here, its declaration (which could name another encoding) is
    # dropped, and the parser is given that text.
    try:
        doc = xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileError("refused: Word XML that is not UTF-8") from exc
    if "\x00" in doc:  # UTF-16/32 decodes as "UTF-8" with NULs, which XML 1.0 forbids
        raise UnsupportedFileError("refused: Word XML that is not UTF-8")
    doc = _XML_DECL.sub("", doc, count=1)
    if "<!DOCTYPE" in doc.upper() or "<!ENTITY" in doc.upper():
        raise UnsupportedFileError("refused: DTD-bearing XML")
    try:
        root = ElementTree.fromstring(doc)
    except ElementTree.ParseError as exc:
        raise UnsupportedFileError("not a readable Word document") from exc
    paragraphs: list[str] = []
    for p in root.iter(f"{_W}p"):
        parts: list[str] = []
        for el in p.iter():
            if el.tag == f"{_W}t":
                parts.append(el.text or "")
            elif el.tag == f"{_W}tab":
                parts.append(" ")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


def plain_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")

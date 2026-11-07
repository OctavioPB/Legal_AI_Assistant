"""Manual inspection: `python -m app.ingestion.cli chunks <file.txt>` (WS2.T09).

Takes plain text until the Docling wrapper (WS2.T02) exists; prints ordinal, node path, tokens,
breadcrumb and char range per chunk, then the oversized / undersized / window report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.ingestion.chunker import Chunker, report
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.tokens import ApproxCounter


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chunks", help="print the chunks of a text file")
    c.add_argument("path", type=Path)
    c.add_argument("--code", default="DOC")
    c.add_argument("--scope", default="FED")
    c.add_argument("--title", default="Documento")
    c.add_argument("--version", default="1")
    pr = sub.add_parser("parse", help="PDF -> cleaned text via Docling")
    pr.add_argument("pdf", type=Path)
    pr.add_argument("-o", "--out", type=Path, required=True)
    pr.add_argument("--pages", help="first-last, e.g. 1-20")
    cl = sub.add_parser("clean", help="re-apply the Docling text cleanup to a saved text file")
    cl.add_argument("path", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "clean":
        from app.ingestion.parser import clean_docling_text_with_report

        raw_path = a.path.with_suffix(".raw.txt")
        if not raw_path.exists():
            sys.stdout.write(f"{a.path.name}: no .raw.txt next to it; re-run `parse` first\n")
            return 1
        cleaned, noise = clean_docling_text_with_report(raw_path.read_text(encoding="utf-8"))
        a.path.write_text(cleaned, encoding="utf-8", newline="\n")
        sys.stdout.write(f"{a.path.name}: cleaned, removed header lines: {noise}\n")
        return 0

    if a.cmd == "parse":
        from app.ingestion.parser import DoclingParser

        rng = tuple(int(x) for x in a.pages.split("-")) if a.pages else None
        doc = DoclingParser().parse(a.pdf, page_range=rng)  # type: ignore[arg-type]
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(doc.text, encoding="utf-8", newline="\n")
        a.out.with_suffix(".raw.txt").write_text(doc.raw_text, encoding="utf-8", newline="\n")
        sys.stdout.write(f"{a.pdf.name}: {doc.page_count} pages, {len(doc.text)} chars\n")
        for w in doc.warnings:
            sys.stdout.write(f"WARNING {w}\n")
        return 0

    src = normalize_text(a.path.read_text(encoding="utf-8"))
    meta = DocMeta(country="MX", scope=a.scope, code=a.code, title=a.title, version_label=a.version)
    chunks = Chunker(ApproxCounter()).chunk(src, parse(src), meta)
    out = sys.stdout
    for i, ch in enumerate(chunks, 1):
        flags = "".join(
            f
            for f, on in (
                ("O", ch.oversized), ("U", ch.undersized), ("W", ch.exceeds_window),
                ("N", ch.is_navigation), ("D", not ch.is_current),
            )
            if on
        )  # fmt: skip
        out.write(
            f"{i:>3} {ch.cite_id}  tok={ch.token_count:<4} [{ch.char_start}:{ch.char_end}] "
            f"{flags}\n    {ch.breadcrumb}\n"
        )
    out.write(f"\n{report(chunks)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    a = ap.parse_args(argv)

    src = normalize_text(a.path.read_text(encoding="utf-8"))
    meta = DocMeta(
        country="MX", scope=a.scope, code=a.code, title=a.title, version_label=a.version
    )
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

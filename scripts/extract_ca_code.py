"""Extract one California code from the official leginfo bulk dump (data/corpus/MANIFEST.md).

    uv run python scripts/extract_ca_code.py LAB data/corpus/raw/CA_LABOR_CODE.txt
    uv run python scripts/extract_ca_code.py LAB out.txt --zip path/to/pubinfo_2025.zip

The extraction itself lives in `app/ingestion/leginfo.py` (shared with the reform watcher, ADR-026),
so the seed and every later check build the text the same way. Remote: HTTP range requests only
(the central directory, LAW_SECTION_TBL.dat and the needed .lob files), never the whole archive.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import date
from typing import Any

from app.ingestion.corpus import LEGINFO
from app.ingestion.leginfo import RangeFile, extract_code, latest_archive
from app.ingestion.watch import UrllibFetcher
from app.settings import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("out")
    ap.add_argument("--zip", help="local zip instead of the remote one")
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args()

    if a.zip:
        z = zipfile.ZipFile(a.zip)
    else:
        s = get_settings()
        f = UrllibFetcher(
            s.reform_watch_user_agent, s.reform_watch_timeout_s, s.reform_watch_max_bytes
        )
        listing = f.get(LEGINFO)
        url = latest_archive(listing.body or b"", LEGINFO)
        head = f.get(url, method="HEAD") if url else None
        if url is None or head is None or not head.size:
            sys.stderr.write(f"no archive found at {LEGINFO} ({listing.status})\n")
            return 1
        rf: Any = RangeFile(f, url, head.size, s.leginfo_max_requests)
        z = zipfile.ZipFile(rf)
    ex = extract_code(z, a.code, date.fromisoformat(a.today))
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ex.text)
    sys.stdout.write(f"{a.code}: {ex.sections} sections written to {a.out}\n")
    for s_ in ex.skipped:
        sys.stdout.write(f"  skipped {s_}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

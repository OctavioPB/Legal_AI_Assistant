"""Extract one California code from the official leginfo bulk dump (data/corpus/MANIFEST.md).

    uv run --with remotezip python scripts/extract_ca_code.py LAB data/corpus/raw/CA_LABOR_CODE.txt
    uv run python scripts/extract_ca_code.py LAB out.txt --zip path/to/pubinfo_2025.zip

Reads only LAW_SECTION_TBL.dat plus the needed .lob files (HTTP range requests when remote).
A section can have several ACTIVE rows (versions with different operative dates). The one in force
TODAY is the row with the greatest EFFECTIVE_DATE <= today; later ones are not yet law and are
reported, not ingested. Rows without an effective date count as in force.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date
from typing import Any

URL = "https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip"


def natural(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.strip(".")) if t]


def clean(v: str) -> str | None:
    return None if v == "NULL" else v.strip("`")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("out")
    ap.add_argument("--zip", help="local zip instead of the remote one")
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args()

    if a.zip:
        import zipfile

        z: Any = zipfile.ZipFile(a.zip)
    else:
        from remotezip import RemoteZip

        z = RemoteZip(URL)
    names = set(z.namelist())

    rows: dict[str, list[list[str]]] = {}
    with z.open("LAW_SECTION_TBL.dat") as f:
        for raw in f:
            c = raw.decode("utf-8", errors="replace").rstrip("\n").split("\t")
            if len(c) >= 16 and clean(c[1]) == a.code and clean(c[15]) == "Y":
                rows.setdefault(clean(c[2]) or "", []).append(c)

    chosen: list[list[str]] = []
    skipped: list[str] = []
    for sec, versions in rows.items():
        eff = [(clean(v[6]) or "0000-00-00")[:10] for v in versions]
        ok = [(e, v) for e, v in zip(eff, versions, strict=True) if e <= a.today]
        if not ok:
            skipped.append(f"{sec}: only future versions {sorted(eff)}")
            continue
        ok.sort(key=lambda p: p[0])
        chosen.append(ok[-1][1])
        for e, _ in ok[:-1]:
            skipped.append(f"{sec}: superseded version effective {e}")
        for e in eff:
            if e > a.today:
                skipped.append(f"{sec}: future version effective {e}")
    chosen.sort(key=lambda c: natural(clean(c[2]) or ""))

    out: list[str] = []
    last: tuple[str | None, ...] | None = None
    for c in chosen:
        xml = z.read(c[14]).decode("utf-8", errors="replace") if c[14] in names else ""
        paras = [
            html.unescape(re.sub(r"<[^>]+>", "", p)).strip()
            for p in re.findall(r"<p[^>]*>(.*?)</p>", xml, re.S)
        ] or [html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()]
        head = tuple(clean(c[i]) for i in (8, 9, 10, 11, 12))
        if head != last:
            d, t, p, ch, ar = (h or "-" for h in head)
            out.append(f"Division {d} Title {t} Part {p} Chapter {ch} Article {ar}")
            last = head
        out.append(f"§ {clean(c[2])} " + "\n".join(p for p in paras if p))
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n\n".join(out) + "\n")
    sys.stdout.write(f"{a.code}: {len(chosen)} sections written to {a.out}\n")
    for s in skipped:
        sys.stdout.write(f"  skipped {s}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

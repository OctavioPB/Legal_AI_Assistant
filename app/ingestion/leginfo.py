"""California's official bulk download (leginfo), read without downloading it (ADR-026).

The legislature publishes the codes only inside one yearly archive (`pubinfo_YYYY.zip`, ~1.2 GB,
updated weekly, ETag + byte ranges). Its web pages answer 403 to automated clients, so the archive
is the official machine-readable route. This module:

* picks the newest yearly archive from the directory listing (`latest_archive`);
* reads the zip through HTTP range requests (`RangeFile`): the central directory, the section table
  (`LAW_SECTION_TBL.dat`, ~6 MB compressed) and only the section texts (`.lob`) it needs;
* rebuilds the code's text exactly as `scripts/extract_ca_code.py` did for the seed
  (`extract_code`), so an unchanged code compares equal;
* keeps a cache of section texts by `.lob` name: a weekly update re-reads only the sections whose
  row changed. The first run reads every section once.
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

_ARCHIVE = re.compile(r'href="(pubinfo_(\d{4})\.zip)"', re.IGNORECASE)
_BLOCK = 16 * 1024  # one request covers a typical section text and its zip header


class _RangeFetcher(Protocol):
    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        method: str = "GET",
        byte_range: tuple[int, int] | None = None,
    ) -> Any: ...


class RangeError(RuntimeError):
    """The server did not honour a byte range (or refused): the extraction stops, nothing is
    guessed."""


def latest_archive(listing: bytes, base_url: str) -> str | None:
    """The newest `pubinfo_YYYY.zip` linked from the official directory listing."""
    found: list[tuple[int, str]] = [
        (int(y), str(name)) for name, y in _ARCHIVE.findall(listing.decode("latin-1"))
    ]
    if not found:
        return None
    return base_url.rstrip("/") + "/" + max(found)[1]


class RangeFile:
    """A read-only, seekable view of a remote file. Every read is a `Range` request (at least
    `_BLOCK` bytes, the last block kept), so zipfile can walk the archive without downloading it."""

    def __init__(self, fetcher: _RangeFetcher, url: str, size: int, max_requests: int) -> None:
        self.fetcher, self.url, self.size, self.max_requests = fetcher, url, size, max_requests
        self.pos = 0
        self.requests = 0
        self._block: tuple[int, bytes] = (0, b"")

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _fetch(self, start: int, end: int) -> bytes:
        if self.requests >= self.max_requests:
            raise RangeError(f"more than {self.max_requests} range requests: stopped")
        self.requests += 1
        res = self.fetcher.get(self.url, byte_range=(start, end))
        if res.status != 206 or res.body is None or len(res.body) != end - start + 1:
            raise RangeError(f"range {start}-{end}: HTTP {res.status} {res.error or ''}".strip())
        body: bytes = res.body
        return body

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        start, data = self._block
        if not (start <= self.pos and self.pos + n <= start + len(data)):
            want = max(n, _BLOCK)
            end = min(self.size, self.pos + want) - 1
            self._block = (self.pos, self._fetch(self.pos, end))
            start, data = self._block
        out = data[self.pos - start : self.pos - start + n]
        self.pos += len(out)
        return out

    def close(self) -> None:
        self._block = (0, b"")


@dataclass
class Extraction:
    text: str
    sections: int
    lob_reads: int  # section texts read from the archive (the rest came from the cache)
    skipped: list[str] = field(default_factory=list)
    cache: dict[str, list[str]] = field(default_factory=dict)


def _natural(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.strip(".")) if t]


def _clean(v: str) -> str | None:
    return None if v == "NULL" else v.strip("`")


def _paragraphs(xml: str) -> list[str]:
    paras = [
        html.unescape(re.sub(r"<[^>]+>", "", p)).strip()
        for p in re.findall(r"<p[^>]*>(.*?)</p>", xml, re.S)
    ]
    return paras or [html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()]


def extract_code(
    z: zipfile.ZipFile, code: str, today: date, cache: Mapping[str, list[str]] | None = None
) -> Extraction:
    """One code's sections in force on `today`, in natural order, with their Division/Title/Part/
    Chapter/Article headers. A section can have several ACTIVE rows (versions with different
    operative dates): the one in force is the row with the greatest EFFECTIVE_DATE <= today; later
    ones are not yet law and are reported, not ingested."""
    cache = cache or {}
    names = set(z.namelist())
    today_s = today.isoformat()
    rows: dict[str, list[list[str]]] = {}
    for raw in z.read("LAW_SECTION_TBL.dat").split(b"\n"):  # rows end in \n only (as the seed)
        c = raw.decode("utf-8", errors="replace").split("\t")
        if len(c) >= 16 and _clean(c[1]) == code and _clean(c[15]) == "Y":
            rows.setdefault(_clean(c[2]) or "", []).append(c)

    chosen: list[list[str]] = []
    skipped: list[str] = []
    for sec, versions in rows.items():
        eff = [(_clean(v[6]) or "0000-00-00")[:10] for v in versions]
        ok = [(e, v) for e, v in zip(eff, versions, strict=True) if e <= today_s]
        if not ok:
            skipped.append(f"{sec}: only future versions {sorted(eff)}")
            continue
        ok.sort(key=lambda p: p[0])
        chosen.append(ok[-1][1])
        skipped.extend(f"{sec}: superseded version effective {e}" for e, _ in ok[:-1])
        skipped.extend(f"{sec}: future version effective {e}" for e in eff if e > today_s)
    chosen.sort(key=lambda c: _natural(_clean(c[2]) or ""))

    out: list[str] = []
    used: dict[str, list[str]] = {}
    reads = 0
    last: tuple[str | None, ...] | None = None
    for c in chosen:
        lob = c[14]
        if lob in cache:
            paras = list(cache[lob])
        else:
            xml = z.read(lob).decode("utf-8", errors="replace") if lob in names else ""
            reads += lob in names
            paras = _paragraphs(xml)
        used[lob] = paras
        head = tuple(_clean(c[i]) for i in (8, 9, 10, 11, 12))
        if head != last:
            d, t, p, ch, ar = (h or "-" for h in head)
            out.append(f"Division {d} Title {t} Part {p} Chapter {ch} Article {ar}")
            last = head
        out.append(f"§ {_clean(c[2])} " + "\n".join(p for p in paras if p))
    return Extraction("\n\n".join(out) + "\n", len(chosen), reads, skipped, used)


def load_cache(path: Path) -> dict[str, list[str]]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [str(p) for p in v] for k, v in data.items() if isinstance(v, list)}


def save_cache(path: Path, cache: Mapping[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


__all__ = ["Extraction", "RangeError", "RangeFile", "extract_code", "latest_archive"]

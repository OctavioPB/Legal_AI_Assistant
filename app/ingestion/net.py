"""HTTP for the only network-using jobs (ADR-017/026/027): one plain HTTPS request, no retries, no
cookies, robots.txt per RFC 9309. Standard library only, so the daily ETL canary
(`app.ingestion.canary`) runs without the ingestion stack."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Protocol


# ---- network -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FetchResult:
    status: int  # HTTP status; 0 = no answer (DNS, timeout, TLS)
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    size: int | None = None  # Content-Length (what a HEAD says the file weighs)


class Fetcher(Protocol):
    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        method: str = "GET",
        byte_range: tuple[int, int] | None = None,
    ) -> FetchResult: ...


class UrllibFetcher:
    """Plain HTTPS client: one request, no retries, no cookies, no header games."""

    def __init__(self, user_agent: str, timeout_s: float, max_bytes: int) -> None:
        self.ua, self.timeout, self.max_bytes = user_agent, timeout_s, max_bytes

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        method: str = "GET",
        byte_range: tuple[int, int] | None = None,
    ) -> FetchResult:
        if urllib.parse.urlsplit(url).scheme != "https":
            return FetchResult(0, error="only https URLs are fetched")
        headers = {"User-Agent": self.ua, "Accept": "application/pdf, text/plain;q=0.9, */*;q=0.1"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if byte_range:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if byte_range and resp.status != 206:  # the range was ignored: do not download all
                    return FetchResult(resp.status, error="the server ignored the byte range")
                body = resp.read(self.max_bytes + 1) if method == "GET" else b""
                if len(body) > self.max_bytes:
                    return FetchResult(resp.status, error="file larger than the configured maximum")
                length = resp.headers.get("Content-Length")
                return FetchResult(
                    resp.status,
                    body,
                    resp.headers.get("ETag"),
                    resp.headers.get("Last-Modified"),
                    size=int(length) if length and length.isdigit() else None,
                )
        except urllib.error.HTTPError as exc:
            return FetchResult(exc.code, error=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult(0, error=f"{type(exc).__name__}: {str(exc)[:200]}")


def robots_allows(fetcher: Fetcher, url: str, user_agent: str) -> bool | None:
    """RFC 9309: 2xx -> obey the rules; 4xx -> no rules (allowed); 5xx / no answer -> disallow
    (None: unknown, skip this run)."""
    parts = urllib.parse.urlsplit(url)
    res = fetcher.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    if 200 <= res.status < 300 and res.body is not None:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(res.body.decode("utf-8", "replace").splitlines())
        return rp.can_fetch(user_agent, url)
    if 400 <= res.status < 500:
        return True
    return None


__all__ = ["FetchResult", "Fetcher", "UrllibFetcher", "robots_allows"]

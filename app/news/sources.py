"""The closed list of news sources (ADR-027) and their readers.

Nothing outside this list is fetched. Every network source is read like the reform watcher reads a
law (https, robots.txt, one request, conditional GET, a block recorded and never evaded). Only what
a source publishes for syndication is kept: title, outlet, date, link and the short summary the feed
or listing itself provides. No article body is fetched or stored.

Checked 2026-11-17: Google News RSS is disallowed by its robots.txt for our client; Proceso and El
Economista answer 403; the DOF did not answer; the Gaceta Parlamentaria answers 403. None is listed.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from xml.etree import ElementTree

from app.ingestion.indexes import TableRows, decode_html

Kind = Literal["reform", "new_law", "recent_reform", "court", "press"]


@dataclass(frozen=True)
class RawItem:
    title: str
    url: str
    outlet: str
    published_at: datetime | None  # None: the source gives no date (first seen is shown instead)
    excerpt: str  # the source's own summary, clipped; never generated


@dataclass(frozen=True)
class NewsSource:
    source_id: str
    outlet: str
    kind: Kind
    weight: float  # official sources rank above press (ADR-027)
    official: bool
    url: str | None = None  # None: built from the database (reforms, official index)
    reader: Literal["rss", "scjn", "internal"] = "internal"
    needs_legal_terms: bool = False  # a general outlet: only items that name legal terms


SOURCES: tuple[NewsSource, ...] = (
    NewsSource("reforms", "Vigilante de reformas", "reform", 3.0, True),
    NewsSource("dip_index", "Cámara de Diputados", "recent_reform", 2.5, True),
    NewsSource("scjn", "Suprema Corte de Justicia de la Nación", "court", 2.5, True,
               url="https://www.scjn.gob.mx/multimedia/comunicados", reader="scjn"),
    NewsSource("juego_corte", "Nexos · El juego de la Corte", "press", 1.5, False,
               url="https://eljuegodelacorte.nexos.com.mx/feed/", reader="rss"),
    NewsSource("elfinanciero", "El Financiero", "press", 1.0, False,
               url="https://www.elfinanciero.com.mx/arc/outboundfeeds/rss/?outputType=xml",
               reader="rss", needs_legal_terms=True),
    NewsSource("jornada_politica", "La Jornada", "press", 1.0, False,
               url="https://www.jornada.com.mx/rss/politica.xml", reader="rss",
               needs_legal_terms=True),
)  # fmt: skip
BY_ID = {s.source_id: s for s in SOURCES}

_TAGS = re.compile(r"<[^>]+>")
_DTD = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


class FeedError(ValueError):
    """A feed that cannot be read safely (DTD/entities, malformed XML)."""


def clean_text(s: str, limit: int) -> str:
    text = " ".join(html.unescape(_TAGS.sub(" ", s or "")).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_rss(body: bytes, outlet: str, excerpt_chars: int) -> list[RawItem]:
    """RSS 2.0 items. A DTD or entity declaration is refused before parsing (no expansion attacks);
    only https links are kept."""
    if _DTD.search(body[:4096]):
        raise FeedError("the feed declares a DTD or entities")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FeedError(f"malformed feed: {exc}") from exc
    out: list[RawItem] = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title") or "", 300)
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        out.append(
            RawItem(
                title=title,
                url=link[:500],
                outlet=outlet,
                published_at=_date(item.findtext("pubDate")),
                excerpt=clean_text(item.findtext("description") or "", excerpt_chars),
            )
        )
    return out


_SCJN_LINK = re.compile(
    r"^https://www\.internet2\.scjn\.gob\.mx/red2/comunicados/comunicado\.asp\?id=\d+$"
)


def parse_scjn(body: bytes, outlet: str) -> list[RawItem]:
    """The SCJN press-release listing: rows of ("Comunicado N", topic with its link). The listing
    carries no date: the pipeline shows when the item was first seen."""
    p = TableRows()
    p.feed(decode_html(body))
    out: list[RawItem] = []
    for row in p.rows:
        if len(row) != 2 or not row[0][0].lower().startswith("comunicado"):
            continue
        links = [h for h in row[1][1] if _SCJN_LINK.match(h)]
        if not links or not row[1][0]:
            continue
        out.append(
            RawItem(
                title=clean_text(row[1][0], 300),
                url=links[0],
                outlet=f"{outlet} · {row[0][0]}",
                published_at=None,
                excerpt="",
            )
        )
    return out


def now() -> datetime:
    return datetime.now(UTC)


__all__ = ["BY_ID", "SOURCES", "FeedError", "NewsSource", "RawItem", "parse_rss", "parse_scjn"]

"""The legal news feed (ADR-027): read only. The pipeline (`python -m app.news.pipeline`, daily with
the reform watcher) writes it; the API's role can never write the public corpus."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import PrincipalDep, StateDep
from app.db.session import tenant_transaction
from app.news.sources import SOURCES

router = APIRouter(prefix="/news")


class NewsItem(BaseModel):
    id: str
    kind: Literal["reform", "new_law", "recent_reform", "court", "press"]
    title: str
    url: str  # https:// (the source) or /reformas?c=... (inside the app)
    outlet: str
    official: bool
    published_at: datetime | None  # None: the source gives no date; first_seen_at is shown
    first_seen_at: datetime
    excerpt: str | None  # the source's own summary, never generated
    terms: list[str]  # the legal terms that put it in the feed
    facts: dict[str, Any]  # reforms: counts by kind of change; index: last reform, note
    score: float


class SourceStatus(BaseModel):
    source_id: str
    outlet: str
    official: bool
    checked_at: datetime | None
    outcome: str | None


class Feed(BaseModel):
    refreshed_at: datetime | None
    items: list[NewsItem]
    sources: list[SourceStatus]


@router.get("", response_model=Feed)
def feed(p: PrincipalDep, st: StateDep) -> Feed:
    limit = st.settings.news_feed_size
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, title, url, outlet, official, published_at, first_seen_at, "
                "excerpt, terms, facts, score, refreshed_at FROM news_items WHERE in_feed "
                "ORDER BY score DESC, coalesce(published_at, first_seen_at) DESC, id LIMIT :n"
            ),
            {"n": limit},
        ).all()
        checks = {
            r.official_id: r
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (official_id) official_id, checked_at, outcome "
                    "FROM source_checks WHERE official_id LIKE 'news:%' "
                    "ORDER BY official_id, checked_at DESC"
                )
            )
        }
    return Feed(
        refreshed_at=max((r.refreshed_at for r in rows), default=None),
        items=[
            NewsItem(
                id=str(r.id),
                kind=r.kind,
                title=r.title,
                url=r.url,
                outlet=r.outlet,
                official=r.official,
                published_at=r.published_at,
                first_seen_at=r.first_seen_at,
                excerpt=r.excerpt,
                terms=list(r.terms),
                facts=dict(r.facts),
                score=r.score,
            )
            for r in rows
        ],
        sources=[
            SourceStatus(
                source_id=s.source_id,
                outlet=s.outlet,
                official=s.official,
                checked_at=c.checked_at if (c := checks.get(f"news:{s.source_id}")) else None,
                outcome=c.outcome if c else None,
            )
            for s in SOURCES
            if s.reader != "internal"
        ],
    )

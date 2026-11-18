"""ADR-027: the legal news feed. Fixed rules choose it, the sources' own words fill it, a block is
recorded and never evaded, and the feed keeps the best N. The network is a fake (sockets are disabled
in the suite); the pages and feeds are real ones, saved and trimmed."""

from __future__ import annotations

import dataclasses
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.discover import record_index
from app.ingestion.indexes import parse_diputados
from app.ingestion.watch import FetchResult
from app.news import pipeline
from app.news.pipeline import Candidate, score_all
from app.news.score import Scored, dedupe, legal_terms
from app.news.sources import BY_ID, SOURCES, FeedError, parse_rss, parse_scjn
from tests.lawtext import WATCH_SETTINGS, FakeFetcher, ok
from tests.test_api import auth, world  # noqa: F401  (fixture)
from tests.test_corpus_discovery import DIP, PAGE, SMALL
from tests.test_retrieval import corpus  # noqa: F401  (fixture)

FIX = Path(__file__).parent / "fixtures" / "news"
SCJN = (FIX / "scjn_comunicados.htm").read_bytes()
NEXOS = (FIX / "nexos_feed.xml").read_bytes()
JORNADA = (FIX / "jornada_politica.xml").read_bytes()
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
S = WATCH_SETTINGS


def url(source_id: str) -> str:
    u = BY_ID[source_id].url
    assert u is not None
    return u


def robots(source_id: str) -> str:
    from urllib.parse import urlsplit

    return f"https://{urlsplit(url(source_id)).netloc}/robots.txt"


def fetcher(**over: FetchResult) -> FakeFetcher:
    routes: dict[str, FetchResult | list[FetchResult]] = {}
    for sid, body in (("scjn", SCJN), ("juego_corte", NEXOS), ("jornada_politica", JORNADA)):
        routes[robots(sid)] = FetchResult(404)
        routes[url(sid)] = over.get(sid, ok(body))
    routes[robots("elfinanciero")] = FetchResult(404)
    routes[url("elfinanciero")] = over.get("elfinanciero", FetchResult(503, error="HTTP 503"))
    return FakeFetcher(routes)


def q(engine: Engine, sql: str, **p: object) -> list:  # type: ignore[type-arg]
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        return list(c.execute(text(sql), p).all())


# ---- readers -------------------------------------------------------------------------------------
def test_the_court_listing_is_read_as_published() -> None:
    items = parse_scjn(SCJN, "SCJN")
    assert len(items) == 4
    first = items[0]
    assert first.outlet == "SCJN · Comunicado 161" and first.published_at is None  # no date given
    assert first.url == "https://www.internet2.scjn.gob.mx/red2/comunicados/comunicado.asp?id=8585"
    assert first.title.startswith("La Suprema Corte avala sanciones")


def test_a_feed_keeps_the_sources_own_short_words_only() -> None:
    items = parse_rss(NEXOS, "Nexos", 280)
    assert len(items) == 2 and all(i.url.startswith("https://") for i in items)
    assert all(i.published_at is not None and i.published_at.tzinfo for i in items)
    assert all(len(i.excerpt) <= 282 and "<" not in i.excerpt for i in items)  # clipped, no markup


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>',
        b"<rss><channel><item><title>x</title></channel>",
    ],
)
def test_an_unsafe_or_broken_feed_is_refused(body: bytes) -> None:
    with pytest.raises(FeedError):
        parse_rss(body, "x", 280)


def test_only_https_links_enter() -> None:
    body = (b"<rss><channel><item><title>Reforma</title><link>http://x.mx/a</link></item>"
            b"<item><title>Reforma</title><link>javascript:alert(1)</link></item></channel></rss>")  # fmt: skip
    assert parse_rss(body, "x", 280) == []


def test_no_source_outside_the_registry_and_none_the_robots_forbid() -> None:
    hosts = {s.url.split("/")[2] for s in SOURCES if s.url}
    assert "news.google.com" not in hosts, "Google News RSS is disallowed by its robots.txt"
    assert all(s.url is None or s.url.startswith("https://") for s in SOURCES)


# ---- relevance -----------------------------------------------------------------------------------
def test_legal_terms_fold_accents_and_respect_word_boundaries() -> None:
    assert legal_terms("LA SUPREMA CORTE resolvió un AMPARO")[0] == ["Suprema Corte", "amparo"]
    assert legal_terms("Una leyenda urbana sobre el codiguito")[1] == 0  # not "ley", not "código"
    assert legal_terms("Acción de inconstitucionalidad contra la reforma")[0][:2] == [
        "reforma", "acción de inconstitucionalidad",
    ]  # fmt: skip
    assert legal_terms("Envía el Ejecutivo una iniciativa de reforma al Senado")[0][:2] == [
        "reforma", "iniciativa",
    ]  # fmt: skip
    assert legal_terms("Crecer exige que la iniciativa privada invierta")[1] == 0
    many = "reforma iniciativa de ley decreto amparo sentencia tribunal jurisprudencia"
    assert legal_terms(many)[1] == 3.0  # capped


def test_the_same_story_from_two_outlets_appears_once() -> None:
    kept = dedupe([
        Scored("https://a/1", "La Corte invalida la reforma judicial", 3.0, (), "scjn"),
        Scored("https://b/2", "La Corte invalida la reforma judicial.", 2.0, (), "jornada"),
        Scored("https://c/3", "Otra nota distinta sobre el Senado", 1.0, (), "jornada"),
    ])  # fmt: skip
    assert [k.key for k in kept] == ["https://a/1", "https://c/3"]
    same_source = dedupe([
        Scored("https://d/1", "LEY de Ingresos de la Federación 2026", 2.0, (), "dip_index"),
        Scored("https://d/2", "LEY de Ingresos de la Federación 2027", 2.0, (), "dip_index"),
    ])  # fmt: skip
    assert len(same_source) == 2, "two laws of one index are two items"


def _cand(source_id: str, title: str, days_ago: float, n: int = 0) -> Candidate:
    src = BY_ID[source_id]
    return Candidate(source_id, src.kind, title, f"https://x/{source_id}/{n}", src.outlet,
                     NOW - timedelta(days=days_ago), "", src.official)  # fmt: skip


def test_rules_rank_official_first_drop_non_legal_press_and_cap_each_source() -> None:
    cands = [
        _cand("jornada_politica", "Cristina Rivera Garza: Timbre", 0),  # no legal term: out
        _cand("jornada_politica", "Decidirá la Corte si atrae un litigio", 0, 1),
        _cand("scjn", "La Suprema Corte resuelve un amparo", 0),
        *(_cand("dip_index", f"LEY número {i} de prueba", 1, i) for i in range(15)),
    ]
    feed = score_all(cands, S, NOW)
    titles = [c.title for c in feed]
    assert "Cristina Rivera Garza: Timbre" not in titles
    assert feed[0].source_id == "scjn"  # official, legal terms, today
    assert sum(1 for c in feed if c.source_id == "dip_index") == S.news_max_per_source
    assert "Decidirá la Corte si atrae un litigio" in titles  # press gets in past the cap
    assert feed[-1].score <= feed[0].score
    small = S.model_copy(update={"news_feed_size": 5})
    assert len(score_all(cands, small, NOW)) == 5


def test_newer_beats_older_from_the_same_source() -> None:
    a = _cand("scjn", "La Suprema Corte resuelve un amparo", 20, 1)
    b = _cand("scjn", "La Suprema Corte resuelve otro amparo en revisión", 1, 2)
    assert [c.url for c in score_all([a, b], S, NOW)] == [b.url, a.url]


# ---- the pipeline --------------------------------------------------------------------------------
def _index_with_recent_reform(engine: Engine) -> None:
    entries = parse_diputados(PAGE, DIP.url)
    recent = [dataclasses.replace(e, last_reform_on=NOW.date() - timedelta(days=3))
              if e.key == "LGAg" else e for e in entries]  # fmt: skip
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        record_index(c, SMALL, recent)


def test_a_run_builds_the_feed_and_records_every_source(ingestor_engine: Engine) -> None:
    _index_with_recent_reform(ingestor_engine)
    f = fetcher(jornada_politica=FetchResult(403, error="HTTP 403"))
    rep = pipeline.run(ingestor_engine, f, S, now=NOW, out=io.StringIO())
    kinds = {c.kind for c in rep.feed}
    assert {"court", "press", "recent_reform"} <= kinds
    assert all(c.source_id != "jornada_politica" for c in rep.feed), "blocked: nothing taken"
    checks = {r.official_id: r.outcome for r in q(ingestor_engine,
              "SELECT official_id, outcome FROM source_checks")}  # fmt: skip
    assert checks == {"news:scjn": "collected", "news:juego_corte": "collected",
                      "news:jornada_politica": "blocked", "news:elfinanciero": "failed"}  # fmt: skip
    assert sum(1 for c in f.calls if c[1] == url("jornada_politica")) == 1, "never retried"
    rows = q(ingestor_engine, "SELECT title, terms, official, in_feed FROM news_items")
    assert len(rows) == len(rep.feed) and all(r.in_feed for r in rows)
    lgag = [r for r in rows if r.title == "LEY General de Aguas"]
    assert lgag and lgag[0].official


def test_the_feed_keeps_the_best_n_retires_the_rest_and_remembers_first_seen(
    ingestor_engine: Engine,
) -> None:
    small = S.model_copy(update={"news_feed_size": 3})
    pipeline.run(ingestor_engine, fetcher(), small, now=NOW, out=io.StringIO())
    first = {r.url: r.first_seen_at for r in q(ingestor_engine,
             "SELECT url, first_seen_at FROM news_items WHERE in_feed")}  # fmt: skip
    assert len(first) == 3
    later = NOW + timedelta(days=1)
    rep = pipeline.run(ingestor_engine, fetcher(), small, now=later, out=io.StringIO())
    again = {r.url: r.first_seen_at for r in q(ingestor_engine,
             "SELECT url, first_seen_at FROM news_items WHERE in_feed")}  # fmt: skip
    for u in set(first) & set(again):
        assert again[u] == first[u], "an item keeps the day it was first seen"
    total = q(ingestor_engine, "SELECT count(*) AS n FROM news_items")[0].n
    assert len(again) == 3 and total >= 3 and rep.added + len(set(first) & set(again)) == 3
    # nothing new and everything 304: the feed stays as it is
    f = fetcher(scjn=FetchResult(304), juego_corte=FetchResult(304),
                jornada_politica=FetchResult(304))  # fmt: skip
    pipeline.run(ingestor_engine, f, small, now=later, out=io.StringIO())
    assert {r.url for r in q(ingestor_engine,
            "SELECT url FROM news_items WHERE in_feed")} == set(again)  # fmt: skip


def test_a_dry_run_writes_nothing(ingestor_engine: Engine) -> None:
    rep = pipeline.run(ingestor_engine, fetcher(), S, now=NOW, out=io.StringIO(), dry_run=True)
    assert rep.feed
    assert q(ingestor_engine, "SELECT count(*) AS n FROM news_items")[0].n == 0
    assert q(ingestor_engine, "SELECT count(*) AS n FROM source_checks")[0].n == 0


def test_the_app_role_cannot_write_the_shared_feed(app_engine: Engine, tenant_a: object) -> None:
    with pytest.raises(Exception, match=r"row-level security"):
        with tenant_transaction(app_engine, tenant_a) as c:  # type: ignore[arg-type]
            c.execute(text(
                "INSERT INTO news_items (tenant_id, source_id, kind, title, url, outlet, score, "
                "official) VALUES (public_corpus_id(), 'scjn', 'court', 'x', 'https://x', 'x', 9, "
                "true)"
            ))  # fmt: skip


def test_dates_are_never_in_the_future_for_scoring() -> None:
    c = _cand("scjn", "La Suprema Corte resuelve un amparo", -5)  # a feed with a future date
    [s] = score_all([c], S, NOW)
    assert s.score <= BY_ID["scjn"].weight + 3.0 + 2.0


# ---- API -------------------------------------------------------------------------------------------


def test_lawyers_read_the_feed_and_nothing_writes_it_from_the_web(world, ingestor_engine) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, *_ = world
    h = auth(client)
    empty = client.get("/news", headers=h).json()
    assert empty["items"] == [] and empty["refreshed_at"] is None
    assert {s["source_id"] for s in empty["sources"]} == {
        "scjn", "juego_corte", "elfinanciero", "jornada_politica",
    }  # fmt: skip
    pipeline.run(ingestor_engine, fetcher(), S, now=datetime.now(UTC), out=io.StringIO())
    got = client.get("/news", headers=h).json()
    scores = [i["score"] for i in got["items"]]
    assert scores and scores == sorted(scores, reverse=True) and len(scores) <= S.news_feed_size
    court = next(i for i in got["items"] if i["kind"] == "court")
    assert court["official"] and court["published_at"] is None and court["terms"]
    by_id = {s["source_id"]: s["outcome"] for s in got["sources"]}
    assert by_id["scjn"] == "collected" and by_id["elfinanciero"] == "failed"
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    assert set(paths["/news"]) == {"get"} and not any(p.startswith("/news/") for p in paths)
    checks = client.get("/reforms", headers=h).json()["checks"]
    assert all(not c["official_id"].startswith("news:") for c in checks), "press is not a law"

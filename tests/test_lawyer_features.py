"""Features for lawyers over the real routes and Postgres+RLS: the law navigator (1), the citation
verifier for external briefs (2, ADR-019), matters shared by membership (5, ADR-018) and reform
alerts driven by the recorded lineage (6, ADR-017)."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.db.session import tenant_transaction
from app.ingestion.corpus import BY_KEY
from app.ingestion.tokens import ApproxCounter
from app.ingestion.watch import FetchResult, Watcher
from tests.lawtext import (
    EMB,
    LFT_URL,
    ROBOTS,
    WATCH_SETTINGS,
    FakeFetcher,
    FakeParser,
    ingest_law,
    ok,
    pdf,
    v1,
    v2,
)
from tests.test_api import add_user, auth, world  # noqa: F401  (fixture)
from tests.test_retrieval import corpus  # noqa: F401  (fixture)

OLD_48 = "MX-FED-LFT-ART48-v2026.05.14"
OLD_FRAC_II = "MX-FED-LFT-ART47-FRACII-v2026.05.14"
NEW_FRAC_II = "MX-FED-LFT-ART47-FRACII-v2026.10.01"


def reform(ingestor_engine: Engine, tmp: Path) -> None:
    """v1 ingested, then the watcher brings v2 (with its lineage)."""
    ingest_law(ingestor_engine, v1())
    w = Watcher(
        settings=WATCH_SETTINGS, engine=ingestor_engine,
        fetcher=FakeFetcher({ROBOTS: FetchResult(404), LFT_URL: ok(pdf(v2()))}),
        parser=FakeParser(), embedder=EMB, counter=ApproxCounter(), inbox=tmp / "in",
        history=tmp / "h", out=io.StringIO(),
    )  # fmt: skip
    assert w.run((BY_KEY["LFT"],))[0].outcome == "ingested"


@pytest.fixture
def law(world, ingestor_engine, tmp_path):  # type: ignore[no-untyped-def]
    reform(ingestor_engine, tmp_path)
    return world


# ── 1. law navigator ─────────────────────────────────────────────────────────────────────────────
def test_the_navigator_browses_a_law_by_its_own_structure(law) -> None:  # type: ignore[no-untyped-def]
    client, *_ = law
    h = auth(client)
    laws = client.get("/laws", headers=h).json()
    [lft] = [x for x in laws if x["official_id"] == "LFT"]
    assert lft["version_label"] == "2026.10.01" and lft["last_reform"] == "2026-10-01"
    toc = client.get("/laws/LFT/toc", headers=h).json()
    titulo = toc[0]
    assert titulo["node_type"] == "titulo"
    assert titulo["heading"] == "Relaciones Individuales de Trabajo"
    assert titulo["label"] == "TITULO SEGUNDO"  # the name once, as the heading
    assert titulo["children"][0]["label"] == "CAPITULO IV"
    capitulo = titulo["children"][0]
    labels = [a["label"] for a in capitulo["children"]]
    assert labels == ["Artículo 47", "Artículo 48", "Artículo 50", "Artículo 50 Bis"]
    assert capitulo["children"][0]["heading"].startswith("Son causas de rescisión")
    found = client.get("/laws/LFT/find", params={"ref": "art. 50 bis"}, headers=h).json()
    assert found["label"] == "Artículo 50 Bis"
    assert client.get("/laws/LFT/find", params={"ref": "999"}, headers=h).status_code == 404
    path47 = client.get("/laws/LFT/find", params={"ref": "47"}, headers=h).json()["path"]
    node = client.get("/laws/LFT/node", params={"path": path47}, headers=h).json()
    assert node["text"].startswith("Artículo 47.-")
    # ADR-049: a heading's name on the next line is part of its label (and of the breadcrumb)
    assert [t["label"] for t in node["trail"]] == [
        "TITULO SEGUNDO Relaciones Individuales de Trabajo",
        "CAPITULO IV Rescisión de las relaciones de trabajo",
    ]
    ids = [c["cite_id"] for c in node["citables"]]
    assert NEW_FRAC_II in ids and "MX-FED-LFT-ART47-FRACIII-v2026.10.01" in ids
    assert node["prev_path"] is None and node["next_path"]
    changed = {r["cite_id"]: r["change"] for r in node["reforms"]}
    assert changed[NEW_FRAC_II] == "amended"
    hist = client.get("/laws/LFT/history", params={"path": path47}, headers=h).json()
    assert [v["version_label"] for v in hist] == ["2026.05.14", "2026.10.01"]
    assert [v["current"] for v in hist] == [False, True]
    assert "acoso" not in hist[0]["text"] and "acoso" in hist[1]["text"]
    for bad in ("x;drop", "", "a..b"):
        assert client.get("/laws/LFT/node", params={"path": bad}, headers=h).status_code == 422
    assert client.get("/laws/NOPE/toc", headers=h).status_code == 404
    assert client.get("/laws/../toc", headers=h).status_code in (404, 422)


def test_a_cited_source_says_when_a_reform_changed_it(law) -> None:  # type: ignore[no-untyped-def]
    client, *_ = law
    h = auth(client)
    old = client.get(f"/sources/{OLD_FRAC_II}", headers=h).json()
    assert old["reform"]["change"] == "amended" and old["reform"]["current"] == NEW_FRAC_II
    assert client.get(f"/sources/{OLD_48}", headers=h).json()["reform"] is None  # unchanged
    assert client.get(f"/sources/{NEW_FRAC_II}", headers=h).json()["reform"] is None


# ── 2. citation verifier (ADR-019) ───────────────────────────────────────────────────────────────
BRIEF = (
    "PRIVILEGIADO: el cliente Juan Pérez confesó los hechos. Con fundamento en el artículo 47, "
    'fracción II, de la Ley Federal del Trabajo, que dispone: "Incurrir el trabajador en faltas de '
    'probidad, honradez o acoso durante sus labores". El artículo 49 de la Ley Federal del '
    "Trabajo exime al patrón. El artículo 50 de la LFT fija la indemnización. El artículo 48 de "
    'la LFT dice "se le indemnice con el importe de seis meses de salario". Véase el artículo 21 '
    "de la Ley del Seguro Social y el artículo 1910 del Código Civil."
)


def test_the_verifier_checks_every_reference_and_keeps_only_the_report(law, admin_engine) -> None:  # type: ignore[no-untyped-def]
    client, *_ = law
    h = auth(client)
    r = client.post("/verify", json={"title": "Demanda contraria", "text": BRIEF}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    by = {f["ref"]: f for f in out["findings"]}
    f47 = by["art. 47, fracc. II, Ley Federal del Trabajo"]
    assert (f47["status"], f47["quote"], f47["cite_ids"]) == ("vigente", "coincide", [NEW_FRAC_II])
    assert by["art. 49, Ley Federal del Trabajo"]["status"] == "no_vigente"  # removed by the reform
    assert by["art. 49, Ley Federal del Trabajo"]["reform"]["change"] == "removed"
    assert by["art. 50, Ley Federal del Trabajo"]["status"] == "derogado"
    assert by["art. 48, Ley Federal del Trabajo"]["quote"] == "no_coincide"  # tres, not seis
    assert by["art. 21, Ley del Seguro Social"]["status"] == "fuera_del_corpus"
    assert by["art. 1910"]["status"] == "ambigua"
    assert out["summary"]["total"] == 6
    start, end = f47["span"]
    assert BRIEF[start:end].lower().startswith("artículo 47")
    # stored: the report, never the brief (no text, no quotation, no client name)
    with admin_engine.connect() as c:
        row = c.execute(text("SELECT title, summary, items FROM verification_reports")).one()
        audit = c.execute(
            text(
                "SELECT query_text, output_text, verdict FROM audit_log "
                "WHERE filters->>'tool' = 'brief_verifier'"
            )
        ).one()
    stored = json.dumps([row.summary, row.items, audit.verdict], ensure_ascii=False)
    for secret in ("Juan Pérez", "confesó", "seis meses", "acoso", "PRIVILEGIADO"):
        assert secret not in stored
    assert audit.query_text is None and audit.output_text is None
    assert "span" not in row.items[0]
    reports = client.get("/verify/reports", headers=h).json()
    assert [x["title"] for x in reports] == ["Demanda contraria"]
    assert (
        client.get(
            f"/verify/reports/{reports[0]['id']}", headers=auth(client, email="mario@a.mx")
        ).status_code
        == 404
    )
    assert client.delete(f"/verify/reports/{reports[0]['id']}", headers=h).status_code == 204


def test_a_pdf_is_parsed_in_memory_and_its_temporary_file_deleted(law) -> None:  # type: ignore[no-untyped-def]
    client, state, *_ = law
    parser = FakeParser()
    state.pdf_parser = parser
    h = auth(client)
    body = pdf("Con fundamento en el artículo 48 de la Ley Federal del Trabajo.")
    r = client.post("/verify/pdf", params={"title": "Escrito"}, content=body,
                    headers={**h, "Content-Type": "application/pdf"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["findings"][0]["status"] == "vigente"
    assert parser.paths and not parser.paths[0].exists(), "the brief must not stay on disk"
    assert client.post("/verify/pdf", params={"title": "x"}, content=b"hello",
                       headers={**h, "Content-Type": "application/pdf"}).status_code == 422  # fmt: skip
    assert client.post("/verify/pdf", params={"title": "x"}, content=body,
                       headers={**h, "Content-Type": "text/plain"}).status_code == 415  # fmt: skip


# ── 5. matters (ADR-018) ─────────────────────────────────────────────────────────────────────────
def users(app_engine: Engine, tenant: uuid.UUID) -> dict[str, str]:
    with tenant_transaction(app_engine, tenant) as c:
        return {r.email: str(r.id) for r in c.execute(text("SELECT id, email FROM users"))}


def test_a_matter_is_shared_with_its_members_only(law, app_engine) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, tenant_a, tenant_b = law
    ana, mario = auth(client), auth(client, email="mario@a.mx")
    bruno = auth(client, "firm-b", "bruno@b.mx")
    m = client.post("/matters", json={"title": "Pérez vs. ACME", "client_ref": "EXP-12"},
                    headers=ana).json()  # fmt: skip
    assert (m["role"], m["members"]) == ("owner", 1)
    conv = client.post("/conversations", json={
        "kind": "chat", "title": "Rescisión", "jurisdiction_id": corpus["fed"], "persona": "labor",
        "matter_id": m["id"]}, headers=ana).json()  # fmt: skip
    private = client.post("/conversations", json={
        "kind": "chat", "title": "Privada", "jurisdiction_id": corpus["fed"], "persona": "labor"},
        headers=ana).json()  # fmt: skip
    # a colleague who is not a member sees nothing of it
    assert client.get("/matters", headers=mario).json() == []
    assert client.get(f"/matters/{m['id']}", headers=mario).status_code == 404
    assert client.get(f"/conversations/{conv['id']}", headers=mario).status_code == 404
    # ... nor can anyone from another firm, even with a valid user id of that firm
    ids_a, ids_b = users(app_engine, tenant_a), users(app_engine, tenant_b)
    assert client.post(f"/matters/{m['id']}/members", json={"user_id": ids_b["bruno@b.mx"]},
                       headers=ana).status_code == 404  # fmt: skip
    assert client.get(f"/matters/{m['id']}", headers=bruno).status_code == 404
    # the owner adds the colleague: now the matter and ITS conversation are shared
    assert client.post(f"/matters/{m['id']}/members", json={"user_id": ids_a["mario@a.mx"]},
                       headers=ana).status_code == 204  # fmt: skip
    detail = client.get(f"/matters/{m['id']}", headers=mario).json()
    assert detail["matter"]["role"] == "member"
    assert {x["email"] for x in detail["members"]} == {"ana@a.mx", "mario@a.mx"}
    shared = client.get("/conversations", params={"matter": m["id"]}, headers=mario).json()
    assert [(c["title"], c["mine"]) for c in shared] == [("Rescisión", False)]
    assert client.get(f"/conversations/{private['id']}", headers=mario).status_code == 404
    # a member works inside the matter but does not manage it, nor rename others' conversations
    note = client.post(f"/matters/{m['id']}/notes", json={"body": "Audiencia el 3 de diciembre"},
                       headers=mario).json()  # fmt: skip
    assert (
        client.patch(f"/matters/{m['id']}", json={"title": "x"}, headers=mario).status_code == 404
    )
    assert client.post(f"/matters/{m['id']}/members", json={"user_id": ids_a["ana@a.mx"]},
                       headers=mario).status_code == 404  # fmt: skip
    assert client.patch(f"/conversations/{conv['id']}", json={"title": "x"},
                        headers=mario).status_code == 404  # fmt: skip
    assert client.delete(f"/matters/{m['id']}/notes/{note['id']}", headers=ana).status_code == 404
    # saved sources: only real, visible ids; the reform badge comes from the lineage
    assert client.post(f"/matters/{m['id']}/sources", json={"cite_id": "MX-FED-NOPE-v1"},
                       headers=mario).status_code == 404  # fmt: skip
    assert client.post(f"/matters/{m['id']}/sources", json={"cite_id": OLD_FRAC_II, "note": "clave"},
                       headers=mario).status_code == 204  # fmt: skip
    [src] = client.get(f"/matters/{m['id']}", headers=ana).json()["sources"]
    assert (src["change"], src["current_cite_id"]) == ("amended", NEW_FRAC_II)
    # the owner deletes the matter: its conversation goes back to being private to its author
    assert client.delete(f"/matters/{m['id']}", headers=mario).status_code == 404
    assert client.delete(f"/matters/{m['id']}", headers=ana).status_code == 204
    assert client.get(f"/conversations/{conv['id']}", headers=mario).status_code == 404
    back = client.get(f"/conversations/{conv['id']}", headers=ana).json()["conversation"]
    assert back["matter_id"] is None


def test_a_conversation_can_be_moved_into_and_out_of_a_matter(law) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, *_ = law
    ana = auth(client)
    m = client.post("/matters", json={"title": "Asunto"}, headers=ana).json()
    c = client.post("/conversations", json={"kind": "research", "title": "Dossier",
                    "jurisdiction_id": corpus["fed"], "persona": "labor"}, headers=ana).json()  # fmt: skip
    moved = client.patch(f"/conversations/{c['id']}", json={"move": True, "matter_id": m["id"]},
                         headers=ana).json()  # fmt: skip
    assert moved["matter_id"] == m["id"] and moved["title"] == "Dossier"
    assert client.get(f"/matters/{m['id']}", headers=ana).json()["matter"]["items"] == 1
    out = client.patch(f"/conversations/{c['id']}", json={"move": True, "matter_id": None},
                       headers=ana).json()  # fmt: skip
    assert out["matter_id"] is None
    assert client.patch(f"/conversations/{c['id']}", json={"move": True,
                        "matter_id": str(uuid.uuid4())}, headers=ana).status_code == 404  # fmt: skip
    assert client.patch(f"/conversations/{c['id']}", json={}, headers=ana).status_code == 422


# ── 6. reform alerts (ADR-017) ───────────────────────────────────────────────────────────────────
def test_alerts_point_at_what_a_reform_touched(law, app_engine) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, tenant_a, _ = law
    ana, mario = auth(client), auth(client, email="mario@a.mx")
    conv = client.post("/conversations", json={"kind": "chat", "title": "Probidad",
                       "jurisdiction_id": corpus["fed"], "persona": "labor"}, headers=ana).json()  # fmt: skip
    uid = users(app_engine, tenant_a)["ana@a.mx"]
    cites = [{"cite_id": OLD_FRAC_II, "citation_string": "LFT, art. 47, fracc. II"},
             {"cite_id": OLD_48, "citation_string": "LFT, art. 48"}]  # fmt: skip
    with tenant_transaction(app_engine, tenant_a, user_id=uuid.UUID(uid)) as c:
        c.execute(
            text("INSERT INTO messages (tenant_id, conversation_id, seq, role, action, content, "
                 "status, citations) VALUES (:t, :c, 1, 'assistant', 'ask', 'x', 'answered', "
                 "CAST(:j AS jsonb))"),
            {"t": tenant_a, "c": conv["id"], "j": json.dumps(cites)},
        )  # fmt: skip
    client.post("/verify", json={"title": "Escrito", "text": "Artículo 49 de la LFT."}, headers=ana)
    alerts = client.get("/reforms/alerts", headers=ana).json()
    got = {(a["where"], a["cite_id"], a["change"]) for a in alerts}
    assert ("conversation", OLD_FRAC_II, "amended") in got
    assert ("report", "MX-FED-LFT-ART49-v2026.05.14", "removed") in got
    assert not any(a["cite_id"] == OLD_48 for a in alerts), "an unchanged unit raises no alert"
    assert client.get("/reforms/alerts", headers=mario).json() == [], "alerts follow RLS"
    ov = client.get("/reforms", headers=ana).json()
    [chg] = ov["changes"]
    assert chg["official_id"] == "LFT" and chg["counts"]["removed"] == 1
    assert ov["checks"][0]["outcome"] == "ingested"
    rows = client.get(f"/reforms/{chg['id']}", headers=ana).json()
    assert {r["change"] for r in rows} == {"amended", "added", "derogated", "removed"}


def test_lawyers_see_what_the_official_indexes_list_read_only(
    law, ingestor_engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """ADR-026: the catalog of an official index, which of it is loaded, and what is new. The API
    has no route that approves: the public corpus is written only by the operator's tool."""
    from tests.test_corpus_discovery import DIP, DIP_ROBOTS, PAGE, SMALL

    client, *_ = law
    ana = auth(client)
    empty = client.get("/reforms/indexes", headers=ana).json()
    assert empty["entries"] == [] and empty["indexes"][0]["checked_at"] is None
    w = Watcher(
        settings=WATCH_SETTINGS, engine=ingestor_engine,
        fetcher=FakeFetcher({DIP_ROBOTS: FetchResult(404), DIP.url: ok(PAGE)}),
        parser=FakeParser(), embedder=EMB, counter=ApproxCounter(), inbox=tmp_path / "in",
        history=tmp_path / "h", out=io.StringIO(),
    )  # fmt: skip
    assert w.read_index(SMALL).outcome == "indexed"
    got = client.get("/reforms/indexes", headers=ana).json()
    [idx] = got["indexes"]
    assert (idx["index_id"], idx["outcome"]) == ("DIP_FED", "indexed")
    by_key = {e["entry_key"]: e for e in got["entries"]}
    assert len(by_key) == 9 and by_key["LFT"]["loaded"] and not by_key["LGAg"]["loaded"]
    assert by_key["LGAg"]["status"] == "available" and by_key["CFPC"]["note"].startswith("(Abrog")
    checks = client.get("/reforms", headers=ana).json()["checks"]
    assert all(not c["official_id"].startswith("index:") for c in checks), "laws only"
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    assert set(paths["/reforms/indexes"]) == {"get"}
    assert not any("approve" in p or "indexes/" in p or "corpus_sources" in p for p in paths)
    assert client.get(f"/reforms/{uuid.uuid4()}", headers=ana).status_code == 404


def test_a_follow_up_after_a_reform_carries_only_unchanged_sources(law, app_engine) -> None:  # type: ignore[no-untyped-def]
    from tests.test_conversations import create, send

    client, _, llm, corpus, tenant_a, _ = law
    ana = auth(client)
    conv = create(client, ana, corpus)
    uid = users(app_engine, tenant_a)["ana@a.mx"]
    cites = [
        {"cite_id": OLD_FRAC_II, "citation_string": "x"},
        {"cite_id": OLD_48, "citation_string": "y"},
    ]
    with tenant_transaction(app_engine, tenant_a, user_id=uuid.UUID(uid)) as c:
        c.execute(
            text("INSERT INTO messages (tenant_id, conversation_id, seq, role, action, content, "
                 "status, citations) VALUES (:t, :c, 1, 'user', 'ask', 'q', NULL, '[]'), "
                 "(:t, :c, 2, 'assistant', 'ask', 'a', 'answered', CAST(:j AS jsonb))"),
            {"t": tenant_a, "c": conv["id"], "j": json.dumps(cites)},
        )  # fmt: skip
    send(client, ana, conv["id"], "reinstalación ante el tribunal")
    rendered = llm.calls[-1]["user"] + llm.calls[-1]["system"]
    assert "MX-FED-LFT-ART48-v2026.10.01" in rendered, "an unchanged source follows its new id"
    assert OLD_FRAC_II not in rendered and OLD_48 not in rendered

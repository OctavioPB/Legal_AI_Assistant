"""The firm's own documents (ADR-025) over the real routes and Postgres+RLS: grammar and extraction,
upload -> citable source, matter scoping (RLS floor AND retrieval scope), deletion, failures."""

from __future__ import annotations

import dataclasses
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.chunker import CF_CITE_RE, Chunker
from app.ingestion.extract import UnsupportedFileError, detect_format, docx_text, plain_text
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.normalizer_generic import normalize_generic_text, parse
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from app.main import create_app
from app.rag.context import load_session_context
from app.rag.jurisdiction import build_jurisdiction_predicate
from app.rag.pipeline import retrieve
from tests.fakes import FakeEmbedder, FakeReranker
from tests.helpers import jurisdiction_id
from tests.lawtext import v1
from tests.test_api import add_user, auth, make_state, settings
from tests.test_chat_service import ScriptedLLM
from tests.test_conversations import send

CONTRACT = """CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES

Contrato que celebran, por una parte, Comercializadora Ejemplo, S.A. de C.V., en lo sucesivo "el
Cliente", y por la otra la licenciada Fulana de Tal, en lo sucesivo "la Prestadora".

DECLARACIONES

I. Declara el Cliente que es una sociedad mercantil constituida conforme a las leyes mexicanas.

II. Declara la Prestadora que cuenta con cédula profesional y con los conocimientos necesarios.

CLÁUSULAS

PRIMERA.- OBJETO. La Prestadora se obliga a prestar al Cliente servicios de asesoría jurídica en
materia laboral, en los términos que este contrato establece.

SEGUNDA.- HONORARIOS. El Cliente pagará a la Prestadora la cantidad mensual de cuarenta mil pesos,
más el impuesto al valor agregado, dentro de los primeros cinco días hábiles de cada mes.

El retraso en el pago causará un interés moratorio del dos por ciento mensual sobre saldos
insolutos, sin necesidad de requerimiento previo.

TERCERA.- VIGENCIA. El presente contrato tendrá una vigencia de doce meses forzosos para ambas
partes, contados a partir de la fecha de su firma.

CUARTA.- PENA CONVENCIONAL. La parte que dé por terminado anticipadamente el contrato pagará a la
otra una pena convencional equivalente a tres meses de honorarios.
"""

META = DocMeta(country="MX", scope="CF", code="DTEST01", title="Contrato de servicios",
               version_label="2026.11.14", client_file="DTEST01", citation_style="plain")  # fmt: skip


# ── grammar (no database) ────────────────────────────────────────────────────────────────────────
def test_a_contract_is_read_by_its_clauses_and_headings() -> None:
    src = normalize_generic_text(CONTRACT)
    nodes = parse(src)
    sections = [n for n in nodes if n.node_type == "articulo"]
    labels = [n.label for n in sections]
    assert labels == [
        "CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES",
        "DECLARACIONES",
        "CLÁUSULAS",
        "PRIMERA.- OBJETO",
        "SEGUNDA.- HONORARIOS",
        "TERCERA.- VIGENCIA",
        "CUARTA.- PENA CONVENCIONAL",
    ]
    # a bare heading travels with the paragraph it introduces; nothing of the text is lost
    assert src[sections[1].start : sections[1].end].startswith("DECLARACIONES\n\nI. Declara")
    covered = "".join(src[n.start : n.end] for n in sections)
    assert "".join(covered.split()) == "".join(src.split())
    assert parse(src) == nodes  # deterministic


def test_chunks_of_a_client_file_are_exact_slices_with_plain_citations() -> None:
    src = normalize_generic_text(CONTRACT)
    counter = ApproxCounter()
    th = ChunkThresholds(min_tokens=20, max_tokens=60, embedder_window=512, counter=counter.name)
    chunks = Chunker(counter, th).chunk(src, parse(src), META)
    ids = [c.cite_id for c in chunks]
    assert len(set(ids)) == len(ids) and all(CF_CITE_RE.match(i) for i in ids)
    assert all(src[c.char_start : c.char_end] == c.text for c in chunks)
    assert "".join("".join(c.text.split()) for c in chunks) == "".join(src.split())
    second = [c for c in chunks if c.cite_id.startswith("CF-DTEST01-S5")]
    assert [c.cite_id for c in second] == ["CF-DTEST01-S5-P1", "CF-DTEST01-S5-P2"]
    assert second[1].citation_string == "«Contrato de servicios», SEGUNDA.- HONORARIOS, párr. 2"


def test_text_without_headings_is_grouped_and_a_wall_of_text_is_cut_at_sentences() -> None:
    para = "El actor prestó sus servicios de manera continua y subordinada para la demandada. "
    loose = normalize_generic_text("\n\n".join(para * 6 for _ in range(12)))
    sections = [n for n in parse(loose) if n.node_type == "articulo"]
    assert len(sections) > 1 and [n.label for n in sections[:2]] == ["Parte 1", "Parte 2"]
    wall = normalize_generic_text(para * 120)  # one block, no line breaks (a badly made PDF)
    pieces = [n for n in parse(wall) if n.node_type == "parrafo"]
    assert len(pieces) >= 2 and all(n.end - n.start <= 4200 for n in pieces)
    assert all(wall[n.start : n.end].rstrip().endswith(".") for n in pieces)


# ── extraction ───────────────────────────────────────────────────────────────────────────────────
def docx(body: str, *, prolog: str = "") -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def docx_of(text_: str) -> bytes:
    return docx(
        "".join(
            f"<w:p><w:r><w:t>{p.replace(chr(10), ' ')}</w:t></w:r></w:p>"
            for p in text_.split("\n\n")
        )
    )


def test_word_text_is_read_from_the_document_body_only() -> None:
    data = docx(
        "<w:p><w:r><w:t>PRIMERA.- OBJETO.</w:t></w:r><w:r><w:tab/><w:t>El texto</w:t></w:r></w:p>"
        "<w:p/><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Celda</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    assert detect_format(data) == "docx"
    assert docx_text(data) == "PRIMERA.- OBJETO. El texto\n\nCelda"


def test_untrusted_files_are_recognised_by_content_and_refused_when_unsafe() -> None:
    assert detect_format(b"%PDF-1.7 ...") == "pdf"
    assert detect_format("Cláusula única".encode()) == "text"
    assert plain_text("Cláusula".encode("cp1252")) == "Cláusula"
    with pytest.raises(UnsupportedFileError):
        detect_format(b"\x7fELF\x00\x00binary")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<x/>")
    with pytest.raises(UnsupportedFileError):
        detect_format(buf.getvalue())  # a zip that is not a Word document
    bomb = docx("<w:p/>", prolog='<!DOCTYPE d [<!ENTITY a "aaaa">]>')
    with pytest.raises(UnsupportedFileError):
        docx_text(bomb)


@pytest.mark.parametrize(
    "encoding", ["utf-16", "utf-16-le", "utf-32", "cp037"], ids=lambda e: f"xml-in-{e}"
)
def test_a_dtd_is_refused_whatever_the_xml_encoding(encoding: str) -> None:
    """Regression (audit 2026-11-17): the DTD guard searched the raw bytes for b"<!DOCTYPE", so
    the same document declared in UTF-16 (or any encoding expat accepts) expanded its entities."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xml = (
        f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE d [<!ENTITY x "SECRETO">]>'
        f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body>'
        "</w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml.encode(encoding))
    with pytest.raises(UnsupportedFileError):
        docx_text(buf.getvalue())


def test_a_word_document_with_a_utf8_bom_and_declaration_is_still_read() -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xml = (
        '﻿<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Cláusula única</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml.encode("utf-8"))
    assert docx_text(buf.getvalue()) == "Cláusula única"


# ── API over Postgres + RLS ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def firm(app_engine, ingestor_engine, provisioner_engine, admin_engine, tenant_a, tenant_b):  # type: ignore[no-untyped-def]
    """Two firms, three users, and a public law written in the SAME generation the API uses."""
    with provisioner_engine.begin() as c:
        c.execute(text("UPDATE tenants SET slug = 'firm-a' WHERE id = :t"), {"t": tenant_a})
        c.execute(text("UPDATE tenants SET slug = 'firm-b' WHERE id = :t"), {"t": tenant_b})
    add_user(app_engine, tenant_a, "ana@a.mx", "admin")
    add_user(app_engine, tenant_a, "mario@a.mx")
    add_user(app_engine, tenant_a, "lucia@a.mx")
    add_user(app_engine, tenant_b, "bruno@b.mx")
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
        nl = jurisdiction_id(c, "MX", "state", "NL")
    counter = ApproxCounter()
    ingest_document(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID,
        meta=DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
                     version_label="2026.05.14"),
        doc_type="ley", jurisdiction_id=fed, source_uri="file://LFT.txt", raw_text=v1(),
        embedder=FakeEmbedder(), counter=counter,
        thresholds=ChunkThresholds.from_settings(settings(), counter.name), official_id="LFT",
    )  # fmt: skip
    state = make_state(app_engine, ScriptedLLM("FAITHFUL"), user_burst=200)
    with TestClient(create_app(state)) as client:
        yield client, state, {"fed": fed, "nl": nl}, tenant_a, tenant_b


def upload(client: TestClient, h: dict[str, str], data: bytes, **params: object):  # type: ignore[no-untyped-def]
    return client.post("/files", params=params, content=data, headers=h)


def matter(client: TestClient, h: dict[str, str], title: str = "Ejemplo vs. Fulana") -> str:
    return str(client.post("/matters", json={"title": title}, headers=h).json()["id"])


def conversation(client: TestClient, h: dict[str, str], jid: int, matter_id: str | None) -> str:
    body = {"kind": "chat", "title": "Contrato", "jurisdiction_id": jid, "persona": "general",
            "matter_id": matter_id}  # fmt: skip
    r = client.post("/conversations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


QUESTION = "pena convencional por terminar anticipadamente el contrato"


def test_an_uploaded_contract_becomes_a_citable_source_of_its_matter(firm) -> None:  # type: ignore[no-untyped-def]
    client, state, j, tenant_a, _ = firm
    ana = auth(client)
    m = matter(client, ana)
    r = upload(client, ana, CONTRACT.encode(), title="  Contrato de   servicios ", kind="contrato",
               jurisdiction_id=j["fed"], matter_id=m)  # fmt: skip
    assert r.status_code == 202, r.text
    f = r.json()
    assert (f["status"], f["title"], f["source_format"], f["mine"], f["can_delete"]) == (
        "ready", "Contrato de servicios", "text", True, True,
    )  # fmt: skip
    assert f["chunk_count"] >= 5 and f["matter_id"] == m
    assert [x["id"] for x in client.get("/files", headers=ana).json()] == [f["id"]]
    units = client.get(f"/files/{f['id']}/text", headers=ana).json()
    assert len(units) == f["chunk_count"] and all(u["cite_id"].startswith("CF-D") for u in units)
    assert "".join("".join(u["text"].split()) for u in units) == "".join(CONTRACT.split())

    events = send(client, ana, conversation(client, ana, j["fed"], m), QUESTION)
    result = events[-1][1]["result"]
    assert result["status"] == "answered", result
    [cite] = result["citations"]
    assert (
        cite["cite_id"].startswith("CF-D")
        and "CUARTA.- PENA CONVENCIONAL" in (cite["citation_string"])
    )
    src = client.get(f"/sources/{cite['cite_id']}", headers=ana).json()
    assert src["is_public"] is False and src["document_title"] == "Contrato de servicios"
    assert "tres meses de honorarios" in src["article_text"]

    # the audit rows carry ids and counts, never the title or the text of the client's document
    with tenant_transaction(state.engine, tenant_a) as c:
        rows = c.execute(
            text("SELECT filters, verdict FROM audit_log WHERE event_type = 'ingestion'")
        ).all()
    assert len(rows) == 1 and rows[0].filters["action"] == "ingest"
    assert "Contrato" not in json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False)


def test_a_matter_file_is_invisible_outside_the_matter_and_outside_the_firm(firm) -> None:  # type: ignore[no-untyped-def]
    client, state, j, tenant_a, _ = firm
    ana, mario = auth(client), auth(client, email="mario@a.mx")
    bruno = auth(client, "firm-b", "bruno@b.mx")
    m = matter(client, ana)
    f = upload(client, ana, CONTRACT.encode(), title="Contrato", jurisdiction_id=j["fed"],
               matter_id=m).json()  # fmt: skip
    cite = client.get(f"/files/{f['id']}/text", headers=ana).json()[0]["cite_id"]
    for other in (mario, bruno):  # a colleague outside the matter, and another firm
        assert client.get("/files", headers=other).json() == []
        assert client.get(f"/files/{f['id']}", headers=other).status_code == 404
        assert client.get(f"/files/{f['id']}/text", headers=other).status_code == 404
        assert client.get(f"/sources/{cite}", headers=other).status_code == 404
        assert client.delete(f"/files/{f['id']}", headers=other).status_code == 404
        assert upload(client, other, b"texto suficiente para el intento", title="x",
                      jurisdiction_id=j["fed"], matter_id=m).status_code == 404  # fmt: skip
    # raw SQL as the colleague, and as the firm with no user anchor at all: zero rows everywhere
    with tenant_transaction(state.engine, tenant_a) as c:
        mario_id = c.execute(text("SELECT id FROM users WHERE email = 'mario@a.mx'")).scalar_one()
    for user in (mario_id, None):
        with tenant_transaction(state.engine, tenant_a, user_id=user) as c:
            for table in ("documents", "doc_nodes", "chunks", "client_files"):
                n = c.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_a}
                ).scalar_one()
                assert n == 0, f"{table} leaked to {'a colleague' if user else 'no user'}"
    # once a member, the colleague sees it (and still cannot delete what is not theirs)
    with tenant_transaction(state.engine, tenant_a) as c:
        c.execute(
            text(
                "INSERT INTO matter_members (tenant_id, matter_id, user_id, role) "
                "VALUES (:t, :m, :u, 'member')"
            ),
            {"t": tenant_a, "m": m, "u": mario_id},
        )
    assert [x["id"] for x in client.get("/files", headers=mario).json()] == [f["id"]]
    assert client.get(f"/sources/{cite}", headers=mario).status_code == 200
    assert client.delete(f"/files/{f['id']}", headers=mario).status_code == 403


def test_one_clients_file_never_enters_another_clients_conversation(firm) -> None:  # type: ignore[no-untyped-def]
    """The lawyer is a member of BOTH matters, so RLS shows both files: the retrieval scope is what
    keeps matter A's contract out of matter B's conversation (and out of a private one)."""
    client, state, j, tenant_a, _ = firm
    ana = auth(client)
    a, b = matter(client, ana, "Cliente A"), matter(client, ana, "Cliente B")
    upload(client, ana, CONTRACT.encode(), title="Contrato A", jurisdiction_id=j["fed"],
           matter_id=a)  # fmt: skip
    with tenant_transaction(state.engine, tenant_a) as c:
        ana_id = c.execute(text("SELECT id FROM users WHERE email = 'ana@a.mx'")).scalar_one()

    def cited(matter_id: str | None) -> set[str]:
        with tenant_transaction(state.engine, tenant_a, user_id=ana_id) as c:
            ctx = load_session_context(
                c, tenant_a, j["fed"], matter_id=uuid.UUID(matter_id) if matter_id else None
            )
            r = retrieve(c, ctx, QUESTION, embedder=FakeEmbedder(), reranker=FakeReranker(),
                         counter=ApproxCounter(), settings=settings())  # fmt: skip
        return {h.cite_id for h in r.hits if h.cite_id.startswith("CF-")}

    assert cited(a), "the matter's own conversation must find its contract"
    assert cited(b) == set() and cited(None) == set()
    # and through the routes: the conversation of matter B answers from the law or refuses
    events = send(client, ana, conversation(client, ana, j["fed"], b), QUESTION)
    assert not any(
        x["cite_id"].startswith("CF-") for x in events[-1][1]["result"].get("citations", [])
    )


def test_a_file_without_a_matter_belongs_to_the_whole_firm_and_follows_jurisdiction(firm) -> None:  # type: ignore[no-untyped-def]
    client, _, j, *_ = firm
    ana, mario = auth(client), auth(client, email="mario@a.mx")
    bruno = auth(client, "firm-b", "bruno@b.mx")
    f = upload(client, ana, docx_of(CONTRACT), title="Machote de servicios", kind="contrato",
               jurisdiction_id=j["nl"]).json()  # fmt: skip
    assert (f["status"], f["source_format"], f["matter_id"]) == ("ready", "docx", None)
    assert [x["id"] for x in client.get("/files", headers=mario).json()] == [f["id"]]
    assert client.get("/files", headers=bruno).json() == []
    # filed under Nuevo León: a NL conversation of a colleague finds it, a federal one does not
    # (stacking goes up, never down)
    in_nl = send(client, mario, conversation(client, mario, j["nl"], None), QUESTION)
    assert any(x["cite_id"].startswith("CF-") for x in in_nl[-1][1]["result"]["citations"])
    in_fed = send(client, mario, conversation(client, mario, j["fed"], None), QUESTION)
    assert not any(
        x["cite_id"].startswith("CF-") for x in in_fed[-1][1]["result"].get("citations", [])
    )
    # the uploader is not Mario, and Mario is not an admin
    assert client.delete(f"/files/{f['id']}", headers=mario).status_code == 403


def test_deleting_a_file_or_its_matter_removes_the_text_and_the_vectors(  # type: ignore[no-untyped-def]
    firm, admin_engine: Engine
) -> None:
    client, _, j, tenant_a, _ = firm
    ana, lucia = auth(client), auth(client, email="lucia@a.mx")
    gone = {"documents": 0, "doc_nodes": 0, "chunks": 0, "client_files": 0}

    def count() -> dict[str, int]:  # the superuser sees through RLS: the real row counts
        with admin_engine.connect() as c:
            return {
                t: c.execute(
                    text(f"SELECT count(*) FROM {t} WHERE tenant_id = :t"), {"t": tenant_a}
                ).scalar_one()
                for t in gone
            }

    own = upload(client, lucia, CONTRACT.encode(), title="De Lucía",
                 jurisdiction_id=j["fed"]).json()  # fmt: skip
    cite = client.get(f"/files/{own['id']}/text", headers=lucia).json()[0]["cite_id"]
    assert count()["chunks"] > 0
    assert client.delete(f"/files/{own['id']}", headers=lucia).status_code == 204
    assert count() == gone
    assert client.get(f"/sources/{cite}", headers=lucia).status_code == 404
    # an admin may delete a colleague's firm-wide file
    other = upload(client, lucia, CONTRACT.encode(), title="Otro",
                   jurisdiction_id=j["fed"]).json()  # fmt: skip
    assert client.delete(f"/files/{other['id']}", headers=ana).status_code == 204
    # deleting a matter deletes its files: they must never fall back to the whole firm
    m = matter(client, ana)
    upload(client, ana, CONTRACT.encode(), title="Del asunto", jurisdiction_id=j["fed"],
           matter_id=m)  # fmt: skip
    assert count()["chunks"] > 0
    assert client.delete(f"/matters/{m}", headers=ana).status_code == 204
    assert count() == gone


def test_bad_uploads_fail_closed_and_leave_nothing_behind(firm) -> None:  # type: ignore[no-untyped-def]
    client, state, j, tenant_a, _ = firm
    ana = auth(client)
    ok = {"title": "x", "jurisdiction_id": j["fed"]}
    assert upload(client, ana, b"\x7fELF\x00\x00\x00", **ok).status_code == 415
    assert upload(client, ana, b"", **ok).status_code == 422
    assert upload(client, ana, b"texto", title="x", jurisdiction_id=999_999).status_code == 422
    assert upload(client, ana, b"texto", title="x", jurisdiction_id=j["fed"],
                  kind="sentencia").status_code == 422  # fmt: skip
    assert upload(client, ana, b"texto", title="x", jurisdiction_id=j["fed"],
                  matter_id=str(uuid.uuid4())).status_code == 404  # fmt: skip
    assert client.post("/files", params=ok, content=b"x").status_code == 401
    state.settings = state.settings.model_copy(update={"client_file_max_bytes": 64})
    assert upload(client, ana, b"a" * 65, **ok).status_code == 413
    state.settings = settings()

    scanned = upload(client, ana, b"   \n  \n", **ok).json()  # nothing to read
    assert (scanned["status"], scanned["error_key"], scanned["chunk_count"]) == (
        "failed", "no_text", None,
    )  # fmt: skip
    # another embedding generation than the live corpus: refused, nothing written (invariant 5)
    good = state.chat
    state.chat = dataclasses.replace(good, embedder=FakeEmbedder(revision="r2"))
    mixed = upload(client, ana, CONTRACT.encode(), **ok).json()
    assert (mixed["status"], mixed["error_key"]) == ("failed", "generation_mismatch")
    state.chat = good
    with tenant_transaction(state.engine, tenant_a) as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :t"), {"t": tenant_a}
            ).scalar_one()
            == 0
        )
    # a failed row can be deleted by its uploader
    assert client.delete(f"/files/{mixed['id']}", headers=ana).status_code == 204


def test_a_job_lost_in_a_restart_is_reported_at_once_not_after_an_hour(firm) -> None:  # type: ignore[no-untyped-def]
    """Regression (audit 2026-11-17): one in-process worker (ADR-025) means a row still
    `processing` from before this process started can never finish. It used to show "Processing"
    for `client_file_stale_minutes` (60) and could not be deleted meanwhile."""
    client, state, j, tenant_a, _ = firm
    ana = auth(client)
    f = upload(client, ana, CONTRACT.encode(), title="Contrato", jurisdiction_id=j["fed"]).json()
    with tenant_transaction(state.engine, tenant_a) as c:  # as if the server died mid-job
        c.execute(
            text(
                "UPDATE client_files SET status = 'processing', created_at = now() - interval '2 minutes' "
                "WHERE id = :id"
            ),
            {"id": f["id"]},
        )
    state.started_at = datetime.now(UTC)  # the process restarted after the upload
    got = client.get(f"/files/{f['id']}", headers=ana).json()
    assert (got["status"], got["error_key"]) == ("failed", "interrupted")
    assert client.delete(f"/files/{f['id']}", headers=ana).status_code == 204
    # a job of THIS process is still processing, however long it takes
    g = upload(client, ana, CONTRACT.encode(), title="Otro", jurisdiction_id=j["fed"]).json()
    with tenant_transaction(state.engine, tenant_a) as c:
        c.execute(
            text("UPDATE client_files SET status = 'processing' WHERE id = :id"), {"id": g["id"]}
        )
    assert client.get(f"/files/{g['id']}", headers=ana).json()["status"] == "processing"


def test_a_matter_file_cannot_be_carried_into_another_matter(firm) -> None:  # type: ignore[no-untyped-def]
    """Regression (audit 2026-11-17). A lawyer in BOTH matters sees both files, so two routes
    could carry client A's contract into matter B, where B's other members would read it:
    moving a conversation of A that cites it to B, and saving one of its units as a source of B."""
    client, _, j, _, _ = firm
    ana = auth(client)
    a, b = matter(client, ana, "Cliente A"), matter(client, ana, "Cliente B")
    f = upload(client, ana, CONTRACT.encode(), title="Contrato A", jurisdiction_id=j["fed"],
               matter_id=a).json()  # fmt: skip
    units = {u["cite_id"] for u in client.get(f"/files/{f['id']}/text", headers=ana).json()}

    # 1. moving a conversation that cited A's file: out of every matter, or into A, only
    conv = conversation(client, ana, j["fed"], a)
    result = send(client, ana, conv, QUESTION)[-1][1]["result"]
    assert {c["cite_id"] for c in result["citations"]} & units, result

    def move(cid: str, m: str | None):  # type: ignore[no-untyped-def]
        return client.patch(
            f"/conversations/{cid}", json={"move": True, "matter_id": m}, headers=ana
        )

    refused = move(conv, b)
    assert refused.status_code == 409 and refused.json()["code"] == "cites_matter_files"
    assert (
        client.get(f"/conversations/{conv}", headers=ana).json()["conversation"]["matter_id"] == a
    )
    assert move(conv, None).status_code == 200  # private to its author, who was in the matter
    assert move(conv, b).status_code == 409  # still cites A's file from outside A
    assert move(conv, a).status_code == 200  # back home is fine
    assert move(conversation(client, ana, j["fed"], a), b).status_code == 200  # cites nothing

    # 2. saved sources: a unit of A goes to A, a firm-wide unit anywhere, never A's into B
    firm_wide = upload(client, ana, b"Modelo del despacho. " + CONTRACT.encode(),
                       title="Modelo del despacho", jurisdiction_id=j["fed"]).json()  # fmt: skip
    shared = client.get(f"/files/{firm_wide['id']}/text", headers=ana).json()[0]["cite_id"]
    unit = sorted(units)[0]

    def save(m: str, cid: str):  # type: ignore[no-untyped-def]
        return client.post(f"/matters/{m}/sources", json={"cite_id": cid}, headers=ana)

    assert save(b, unit).status_code == 404
    assert save(a, unit).status_code == 204
    assert save(b, shared).status_code == 204


def test_a_law_the_corpus_lacks_can_be_uploaded_and_is_cited_as_a_law(firm) -> None:  # type: ignore[no-untyped-def]
    client, _, j, *_ = firm
    ana = auth(client)
    law = (
        "REGLAMENTO INTERIOR DE TRABAJO\n\n"
        "Artículo 1.- El presente reglamento es obligatorio para todo el personal.\n\n"
        "Artículo 2.- La jornada de trabajo será de ocho horas diarias.\n\n"
        "Artículo 3.- Se concederán treinta minutos para tomar alimentos.\n\n"
        "Artículo 4.- Las faltas injustificadas se sancionarán con amonestación."
    )
    f = upload(client, ana, law.encode(), title="Reglamento interior", kind="norma",
               jurisdiction_id=j["fed"]).json()  # fmt: skip
    assert f["status"] == "ready"
    units = client.get(f"/files/{f['id']}/text", headers=ana).json()
    assert any(u["citation_string"] == "Reglamento interior, art. 2" for u in units)
    # declared as a law but without articles: read as a plain document, never forced into one
    g = upload(client, ana, CONTRACT.encode(), title="No es ley", kind="norma",
               jurisdiction_id=j["fed"]).json()  # fmt: skip
    units = client.get(f"/files/{g['id']}/text", headers=ana).json()
    assert g["status"] == "ready" and all("art." not in u["citation_string"] for u in units)


def test_the_matter_scope_is_part_of_the_hard_prefilter(firm) -> None:  # type: ignore[no-untyped-def]
    _, state, j, tenant_a, _ = firm
    m = uuid.uuid4()
    with tenant_transaction(state.engine, tenant_a) as c:
        plain = build_jurisdiction_predicate(load_session_context(c, tenant_a, j["fed"]))
        scoped = build_jurisdiction_predicate(
            load_session_context(c, tenant_a, j["fed"], matter_id=m)
        )
    assert plain.sql.endswith("chunks.matter_id IS NULL") and "j_matter" not in plain.params
    assert "chunks.matter_id = CAST(:j_matter AS uuid)" in scoped.sql
    assert scoped.params["j_matter"] == str(m)
    json.dumps(scoped.params)  # the predicate is logged: it must stay serializable


# ── scanned PDFs (ADR-030) ───────────────────────────────────────────────────────────────────────
SCANNED_CLAUSE = (
    "QUINTA.- CONFIDENCIALIDAD. La Prestadora guardará reserva absoluta sobre la información del "
    "Cliente durante la vigencia del contrato y cinco años después de su terminación."
)
POOR_CLAUSE = "SEXTA.- JURISDICCIÓN. Las partes se someten a los tribunales de la Ciudad de México."


class _Parser:
    """Docling stand-in: the digital pages' text."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def parse(self, path: object, *, page_range: object = None) -> object:
        from types import SimpleNamespace

        self.calls.append(page_range)
        return SimpleNamespace(text=CONTRACT, warnings=())


class _Reader:
    """Tesseract stand-in: page 2 reads well, page 3 is a poor scan."""

    label = "fake-ocr 1.0 spa+eng"

    def __call__(self, page: object, number: int) -> object:
        from app.ingestion.ocr import OcrPageText

        if number == 3:
            return OcrPageText(number, POOR_CLAUSE, confidence=0.35, words=14)
        return OcrPageText(number, SCANNED_CLAUSE, confidence=0.91, words=28)


def _scanned_pdf(pages: list[str]) -> bytes:
    from tests.pdf_fixtures import build_pdf, scan_image

    return build_pdf([scan_image(["x"]) if p == "scan" else p for p in pages])


def test_a_scanned_page_is_read_by_ocr_and_a_poor_one_never_becomes_citable(firm) -> None:  # type: ignore[no-untyped-def]
    client, state, j, tenant_a, _ = firm
    state.pdf_parser, state.page_reader = _Parser(), _Reader()
    ana = auth(client)
    data = _scanned_pdf(["Contrato con capa de texto real.", "scan", "scan"])
    f = upload(client, ana, data, title="Contrato escaneado", kind="contrato",
               jurisdiction_id=j["fed"]).json()  # fmt: skip
    assert (f["status"], f["pages"], f["ocr_pages"], f["ocr_excluded_pages"]) == (
        "ready", 3, [2], [3],
    )  # fmt: skip
    assert f["ocr_confidence"] == pytest.approx(0.91)
    units = client.get(f"/files/{f['id']}/text", headers=ana).json()
    joined = " ".join(u["text"] for u in units)
    assert "reserva absoluta" in joined  # the OCR page is citable...
    assert "JURISDICCIÓN" not in joined  # ...the poor page is not, anywhere
    ocr_unit = next(u for u in units if "reserva absoluta" in u["text"])
    src = client.get(f"/sources/{ocr_unit['cite_id']}", headers=ana).json()
    assert (src["ocr_pages"], src["ocr_confidence"]) == ([2], pytest.approx(0.91))
    with tenant_transaction(state.engine, tenant_a) as c:
        engine = c.execute(
            text("SELECT ocr_engine FROM client_files WHERE id = :i"), {"i": f["id"]}
        ).scalar_one()
        verdict = c.execute(
            text("SELECT verdict FROM audit_log WHERE event_type = 'ingestion'")
        ).scalar_one()
    assert engine == "fake-ocr 1.0 spa+eng"
    assert (verdict["ocr_pages"], verdict["ocr_excluded_pages"]) == (1, 1)  # counts, never text


def test_reading_as_scanned_skips_the_text_layer_and_bad_scans_fail_closed(firm) -> None:  # type: ignore[no-untyped-def]
    client, state, j, _, _ = firm
    parser = _Parser()
    state.pdf_parser, state.page_reader = parser, _Reader()
    ana = auth(client)
    ok = {"title": "x", "jurisdiction_id": j["fed"]}
    forced = upload(client, ana, _scanned_pdf(["texto con capa danada", "otra pagina mas"]),
                    force_ocr="true", **ok).json()  # fmt: skip
    assert (forced["status"], forced["ocr_pages"]) == ("ready", [1, 2])
    assert parser.calls == []  # the damaged text layer was never used
    # a digital PDF ignores nothing: force_ocr on a text file is meaningless and ignored
    plain = upload(client, ana, CONTRACT.encode(), force_ocr="true", **ok).json()
    assert (plain["status"], plain["ocr_pages"]) == ("ready", [])

    state.page_reader = lambda page, n: _Reader()(page, 3)  # every page reads poorly
    poor = upload(client, ana, _scanned_pdf(["scan"]), **ok).json()
    assert (poor["status"], poor["error_key"], poor["chunk_count"]) == (
        "failed", "ocr_low_confidence", None,
    )  # fmt: skip

    state.page_reader = None  # no Tesseract on this machine
    state.settings = state.settings.model_copy(update={"ocr_tesseract_cmd": "Z:/no/tesseract.exe"})
    missing = upload(client, ana, _scanned_pdf(["scan"]), **ok).json()
    assert (missing["status"], missing["error_key"]) == ("failed", "ocr_unavailable")
    state.settings = state.settings.model_copy(update={"ocr_max_pages": 0})
    state.page_reader = _Reader()
    many = upload(client, ana, _scanned_pdf(["scan"]), **ok).json()
    assert (many["status"], many["error_key"]) == ("failed", "too_many_scanned_pages")
    state.settings = settings()

"""The firm's own documents (ADR-025): upload a contract, a brief, a ruling or a law that is not in
the public corpus, and cite it in conversations like any other source.

* The file is read in memory, turned into text and discarded: the ORIGINAL is never stored. What is
  kept is the extracted text (doc_nodes, chunks) under the firm's RLS, and under the matter's RLS
  when the file belongs to a matter.
* Processing runs in one background thread; `client_files.status` is what the page polls.
* Same embedding generation and thresholds as the live corpus (invariant 5): the ingestion refuses
  to mix.
* Deleting a file deletes its text and vectors. A conversation that cited it keeps the citation's
  name; the source itself is gone (404).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Connection, text
from starlette.concurrency import run_in_threadpool

from app.api.auth import Principal
from app.api.conversations import check_matter
from app.api.deps import AppState, PrincipalDep, StateDep
from app.api.verify import _parse_pdf
from app.db.session import tenant_transaction
from app.ingestion.extract import (
    Format,
    UnsupportedFileError,
    detect_format,
    docx_text,
    plain_text,
)
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.normalizer_mx import normalize_text
from app.ingestion.normalizer_mx import parse as parse_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.normalizer_us import parse as parse_us
from app.ingestion.store import VersionMismatchError, ingest_document
from app.orchestration.resilient import ProviderError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/files")

Kind = Literal["contrato", "escrito", "resolucion", "norma", "otro"]
TITLE_MAX = 200
TEXT_UNITS_MAX = 3000

_COLS = (
    "f.id, f.title, f.kind, f.language, f.jurisdiction_id, f.matter_id, f.status, f.error_key, "
    "f.grammar, f.pages, f.chunk_count, f.source_format, f.created_at, f.finished_at, "
    "f.uploaded_by = app_user_id() AS mine, u.display_name AS uploaded_by_name, "
    "f.document_id, f.file_token"
)
_FROM = "FROM client_files f JOIN users u ON u.id = f.uploaded_by AND u.tenant_id = f.tenant_id"


class FileOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: Kind
    language: Literal["es", "en"]
    jurisdiction_id: int
    matter_id: uuid.UUID | None
    status: Literal["processing", "ready", "failed"]
    error_key: str | None
    pages: int | None
    chunk_count: int | None
    source_format: Format
    created_at: datetime
    mine: bool
    uploaded_by_name: str
    can_delete: bool


class FileUnit(BaseModel):
    cite_id: str
    citation_string: str
    text: str


def _out(row: Any, p: Principal, st: AppState) -> FileOut:
    state, error = row.status, row.error_key
    age = (datetime.now(UTC) - row.created_at).total_seconds() / 60
    lost = row.created_at < st.started_at  # queued before this process: the job died with it
    if state == "processing" and (lost or age > st.settings.client_file_stale_minutes):
        state, error = "failed", "interrupted"  # the server stopped mid-job: nothing was written
    return FileOut(
        id=row.id, title=row.title, kind=row.kind, language=row.language,
        jurisdiction_id=row.jurisdiction_id, matter_id=row.matter_id, status=state,
        error_key=error, pages=row.pages, chunk_count=row.chunk_count,
        source_format=row.source_format, created_at=row.created_at, mine=row.mine,
        uploaded_by_name=row.uploaded_by_name,
        can_delete=row.mine or p.role in ("owner", "admin"),
    )  # fmt: skip


def _one(conn: Connection, file_id: uuid.UUID) -> Any:
    row = conn.execute(text(f"SELECT {_COLS} {_FROM} WHERE f.id = :id"), {"id": file_id}).first()
    if row is None:  # RLS: another firm's file, or a matter the caller is not in
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return row


@router.get("", response_model=list[FileOut])
def list_files(p: PrincipalDep, st: StateDep, matter: uuid.UUID | None = None) -> list[FileOut]:
    """Every file the caller can see (the firm's, and those of the caller's matters); `matter`
    narrows to one matter."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                f"SELECT {_COLS} {_FROM} "
                "WHERE (CAST(:m AS uuid) IS NULL OR f.matter_id = CAST(:m AS uuid)) "
                "ORDER BY f.created_at DESC, f.id LIMIT 500"
            ),
            {"m": matter},
        ).all()
    return [_out(r, p, st) for r in rows]


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> FileOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        return _out(_one(conn, file_id), p, st)


@router.get("/{file_id}/text", response_model=list[FileUnit])
def file_text(file_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> list[FileUnit]:
    """The text as it was extracted, unit by unit, so the lawyer can check it before relying on
    it (a scanned page or a bad column order shows here, not in an answer)."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        row = _one(conn, file_id)
        if row.document_id is None:
            return []
        units = conn.execute(
            text(
                "SELECT cite_id, citation_string, text FROM chunks WHERE document_id = :d "
                "ORDER BY char_start, cite_id LIMIT :k"
            ),
            {"d": row.document_id, "k": TEXT_UNITS_MAX},
        ).all()
    return [FileUnit(cite_id=u.cite_id, citation_string=u.citation_string, text=u.text)
            for u in units]  # fmt: skip


@router.post("", response_model=FileOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    request: Request,
    p: PrincipalDep,
    st: StateDep,
    title: str = Query(min_length=1, max_length=TITLE_MAX),
    kind: Kind = "otro",
    language: Literal["es", "en"] = "es",
    jurisdiction_id: int = Query(ge=1),
    matter_id: uuid.UUID | None = None,
) -> FileOut:
    """Body: the file's bytes (PDF, .docx or plain text; recognised by content). 202: the row is
    `processing`; poll `GET /files/{id}`."""
    st.limits.check(str(p.tenant_id), str(p.user_id))
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > st.settings.client_file_max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "empty file")
    payload = bytes(data)  # the one immutable copy (up to 25 MB): the buffer is freed right away
    del data
    try:
        fmt = detect_format(payload)
    except UnsupportedFileError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported file") from exc
    title = " ".join(title.split())
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "title required")
    token = "D" + uuid.uuid4().hex[:11].upper()
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        check_matter(conn, matter_id)
        j = conn.execute(
            text("SELECT country FROM jurisdictions WHERE id = :i"), {"i": jurisdiction_id}
        ).first()
        if j is None:  # closed catalog: never a free-text place
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown jurisdiction")
        file_id: uuid.UUID = conn.execute(
            text(
                "INSERT INTO client_files (tenant_id, uploaded_by, matter_id, title, kind, "
                "language, jurisdiction_id, file_token, source_format) "
                "VALUES (:t, :u, :m, :title, :k, :l, :j, :tok, :f) RETURNING id"
            ),
            {"t": p.tenant_id, "u": p.user_id, "m": matter_id, "title": title, "k": kind,
             "l": language, "j": jurisdiction_id, "tok": token, "f": fmt},
        ).scalar_one()  # fmt: skip
    job = _Job(file_id=file_id, tenant_id=p.tenant_id, user_id=p.user_id, title=title, kind=kind,
               language=language, jurisdiction_id=jurisdiction_id, country=j.country,
               matter_id=matter_id, token=token, fmt=fmt)  # fmt: skip
    await run_in_threadpool(st.jobs.submit, lambda: process(st, job, payload))
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        return _out(_one(conn, file_id), p, st)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> Response:
    """Its uploader or a firm admin. Deletes the extracted text and vectors with it."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        row = _one(conn, file_id)
        out = _out(row, p, st)
        if not out.can_delete:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only the uploader or an admin")
        if out.status == "processing":
            raise HTTPException(status.HTTP_409_CONFLICT, "still processing")
        if row.document_id is not None:  # cascades to doc_nodes, chunks and this row
            conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": row.document_id})
        conn.execute(text("DELETE FROM client_files WHERE id = :id"), {"id": file_id})
        _audit(conn, p.tenant_id, p.user_id, {"action": "delete", "file_id": str(file_id)}, {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── processing (background thread) ────────────────────────────────────────────────────────────────
class _Job(BaseModel):
    file_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    kind: Kind
    language: Literal["es", "en"]
    jurisdiction_id: int
    country: str
    matter_id: uuid.UUID | None
    token: str
    fmt: Format


class _Failed(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _audit(
    conn: Connection, tenant: uuid.UUID, user: uuid.UUID, filters: dict[str, Any],
    verdict: dict[str, Any],
) -> None:  # fmt: skip
    """Counts and ids only: never the title or the text of a client's document."""
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, user_id, event_type, filters, verdict) "
            "VALUES (:t, :u, 'ingestion', CAST(:f AS jsonb), CAST(:v AS jsonb))"
        ),
        {"t": tenant, "u": user, "f": json.dumps({"tool": "client_file", **filters}),
         "v": json.dumps(verdict)},
    )  # fmt: skip


def choose_grammar(
    kind: str, language: str, raw: str, min_articles: int
) -> Literal["mx", "us", "generic"]:
    """A document declared as a law is read with the statute grammar only if that grammar really
    finds articles in it; anything else is read by headings and paragraphs."""
    if kind != "norma":
        return "generic"
    if language == "en":
        found = sum(n.node_type == "articulo" for n in parse_us(normalize_us_text(raw)))
        return "us" if found >= min_articles else "generic"
    found = sum(n.node_type == "articulo" for n in parse_mx(normalize_text(raw)))
    return "mx" if found >= min_articles else "generic"


def _extract(st: AppState, job: _Job, data: bytes) -> tuple[str, int | None]:
    if job.fmt == "pdf":
        try:
            parsed, _warnings = _parse_pdf(data, st)
        except Exception as exc:  # a damaged or encrypted PDF: the parser's own errors vary
            log.warning("client file %s: PDF could not be parsed (%s)", job.file_id,
                        type(exc).__name__)  # fmt: skip
            raise _Failed("unreadable") from exc
        return parsed, None
    if job.fmt == "docx":
        try:
            return docx_text(data), None
        except UnsupportedFileError as exc:
            raise _Failed("unreadable") from exc
    return plain_text(data), None


def process(st: AppState, job: _Job, data: bytes) -> None:
    """Never raises: the outcome is the row's status. Nothing of the file survives a failure."""
    s = st.settings
    try:
        raw, pages = _extract(st, job, data)
        del data
        if len(raw.strip()) < 20:
            raise _Failed("no_text")  # typically a scanned PDF: there is no text layer to read
        if len(raw) > s.client_file_max_chars:
            raise _Failed("too_long")
        grammar = choose_grammar(job.kind, job.language, raw, s.client_file_min_articles)
        meta = DocMeta(
            country=job.country, scope="CF", code=job.token, title=job.title,
            version_label=datetime.now(UTC).strftime("%Y.%m.%d"), language=job.language,
            client_file=job.token, citation_style="plain" if grammar == "generic" else "legal",
        )  # fmt: skip
        counter = st.chat.counter
        try:
            stats = ingest_document(
                st.engine, job.tenant_id, meta=meta, doc_type="client_file",
                jurisdiction_id=job.jurisdiction_id, source_uri=f"client-file://{job.file_id}",
                raw_text=raw, embedder=st.chat.embedder, counter=counter,
                thresholds=ChunkThresholds.from_settings(s, counter.name), grammar=grammar,
                matter_id=job.matter_id, user_id=job.user_id,
            )  # fmt: skip
        except VersionMismatchError as exc:
            raise _Failed("generation_mismatch") from exc
        except ProviderError as exc:
            raise _Failed("embedder_unavailable") from exc
        except ValueError as exc:  # "no chunks produced": nothing citable in the text
            raise _Failed("no_text") from exc
        with tenant_transaction(st.engine, job.tenant_id, user_id=job.user_id) as conn:
            done = conn.execute(
                text(
                    "UPDATE client_files SET status = 'ready', document_id = :d, chunk_count = :n, "
                    "pages = :p, grammar = :g, finished_at = now() WHERE id = :id"
                ),
                {"d": stats.document_id, "n": stats.chunks_written, "p": pages, "g": grammar,
                 "id": job.file_id},
            ).rowcount  # fmt: skip
            if not done:  # the row vanished meanwhile (matter deleted): leave no orphan text
                conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": stats.document_id})
                return
            _audit(
                conn, job.tenant_id, job.user_id,
                {"action": "ingest", "file_id": str(job.file_id), "format": job.fmt,
                 "grammar": grammar},
                {"chunks": stats.chunks_written, "oversized": len(stats.oversized),
                 "undersized": len(stats.undersized), "seconds": round(stats.seconds, 2)},
            )  # fmt: skip
    except Exception as exc:
        key = exc.key if isinstance(exc, _Failed) else "internal"
        if key == "internal":
            log.exception("client file %s failed", job.file_id)
        try:
            with tenant_transaction(st.engine, job.tenant_id, user_id=job.user_id) as conn:
                conn.execute(
                    text(
                        "UPDATE client_files SET status = 'failed', error_key = :k, "
                        "finished_at = now() WHERE id = :id"
                    ),
                    {"k": key, "id": job.file_id},
                )
        except Exception:
            log.exception("client file %s: could not record the failure", job.file_id)

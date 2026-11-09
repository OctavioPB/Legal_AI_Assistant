"""Citation verifier for external briefs (feature 2, ADR-019).

The brief (pasted text or a PDF) is processed in memory and never stored: a PDF is written to a
temporary file only for the parser and deleted in `finally`. What is kept is the REPORT (normalized
references, statuses, cite ids; no text or quotation of the brief) and an audit row with counts.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.api.conversations import check_matter
from app.api.deps import PrincipalDep, StateDep
from app.corpus.verify import MAX_TEXT_CHARS, Finding, summarize, verify
from app.db.session import tenant_transaction

router = APIRouter(prefix="/verify")

MAX_PDF_BYTES = 15 * 1024 * 1024
TITLE_MAX = 200


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    matter_id: uuid.UUID | None = None


class VerifyOut(BaseModel):
    report_id: uuid.UUID
    title: str
    summary: dict[str, int]
    findings: list[Finding]
    text: str | None = (
        None  # the PDF's extracted text, returned once for highlighting, never stored
    )
    warnings: list[str] = []


class ReportOut(BaseModel):
    id: uuid.UUID
    title: str
    source_kind: str
    matter_id: uuid.UUID | None
    mine: bool
    summary: dict[str, int]
    items: list[dict[str, Any]]
    created_at: datetime


def _stored(findings: list[Finding]) -> list[dict[str, Any]]:
    """What is kept of a finding: no span (a position in the brief), nothing of the brief."""
    return [f.model_dump(mode="json", exclude={"span"}) for f in findings]


def _save(
    conn: Connection,
    p: Any,
    title: str,
    kind: str,
    matter_id: uuid.UUID | None,
    findings: list[Finding],
) -> uuid.UUID:
    summary = summarize(findings)
    rid: uuid.UUID = conn.execute(
        text(
            "INSERT INTO verification_reports (tenant_id, user_id, matter_id, title, source_kind, "
            "summary, items) VALUES (:t, :u, :m, :title, :k, CAST(:s AS jsonb), CAST(:i AS jsonb)) "
            "RETURNING id"
        ),
        {
            "t": p.tenant_id,
            "u": p.user_id,
            "m": matter_id,
            "title": title.strip(),
            "k": kind,
            "s": json.dumps(summary),
            "i": json.dumps(_stored(findings), ensure_ascii=False),
        },
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, user_id, event_type, filters, retrieved_cite_ids, "
            "verdict) VALUES (:t, :u, 'validation', CAST(:f AS jsonb), :ids, CAST(:v AS jsonb))"
        ),
        {
            "t": p.tenant_id,
            "u": p.user_id,
            "f": json.dumps({"tool": "brief_verifier", "source_kind": kind}),
            "ids": sorted({c for f in findings for c in f.cite_ids}),
            "v": json.dumps({"report_id": str(rid), "summary": summary}),
        },
    )
    if matter_id is not None:
        conn.execute(text("UPDATE matters SET updated_at = now() WHERE id = :m"), {"m": matter_id})
    return rid


def _run(
    st: Any, p: Any, title: str, brief: str, kind: str, matter_id: uuid.UUID | None
) -> tuple[uuid.UUID, list[Finding]]:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        check_matter(conn, matter_id)
        findings = verify(conn, brief, min_words=st.settings.validator_quote_min_words)
        rid = _save(conn, p, title, kind, matter_id, findings)
    return rid, findings


@router.post("", response_model=VerifyOut)
def verify_text(body: VerifyIn, p: PrincipalDep, st: StateDep) -> VerifyOut:
    st.limits.check(str(p.tenant_id), str(p.user_id))
    rid, findings = _run(st, p, body.title, body.text, "text", body.matter_id)
    return VerifyOut(
        report_id=rid, title=body.title, summary=summarize(findings), findings=findings
    )


@router.post("/pdf", response_model=VerifyOut)
async def verify_pdf(
    request: Request,
    p: PrincipalDep,
    st: StateDep,
    title: str = Query(min_length=1, max_length=TITLE_MAX),
    matter_id: uuid.UUID | None = None,
) -> VerifyOut:
    """Body: the PDF bytes (`Content-Type: application/pdf`). Parsed locally (Docling, ADR-004)."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/pdf":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "a PDF is required")
    st.limits.check(str(p.tenant_id), str(p.user_id))
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "PDF too large")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a PDF")
    from starlette.concurrency import run_in_threadpool

    parsed_text, warnings = await run_in_threadpool(_parse_pdf, bytes(data), st)
    del data
    if not parsed_text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no text in the PDF")
    rid, findings = await run_in_threadpool(
        _run, st, p, title, parsed_text[:MAX_TEXT_CHARS], "pdf", matter_id
    )
    return VerifyOut(
        report_id=rid,
        title=title,
        summary=summarize(findings),
        findings=findings,
        text=parsed_text[:MAX_TEXT_CHARS],
        warnings=warnings,
    )


def _parse_pdf(data: bytes, st: Any) -> tuple[str, list[str]]:
    parser = getattr(st, "pdf_parser", None)
    if parser is None:
        from app.ingestion.parser import DoclingParser

        parser = st.pdf_parser = DoclingParser()
    fd, name = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        doc = parser.parse(Path(name))
        return doc.text, list(doc.warnings)
    finally:
        Path(name).unlink(missing_ok=True)  # ADR-019: the brief is never kept


_REPORT_COLS = (
    "id, title, source_kind, matter_id, user_id = app_user_id() AS mine, summary, items, created_at"
)


def _report(r: Any) -> ReportOut:
    return ReportOut(
        id=r.id,
        title=r.title,
        source_kind=r.source_kind,
        matter_id=r.matter_id,
        mine=r.mine,
        summary=dict(r.summary),
        items=list(r.items),
        created_at=r.created_at,
    )


@router.get("/reports", response_model=list[ReportOut])
def list_reports(p: PrincipalDep, st: StateDep) -> list[ReportOut]:
    """The caller's own reports (a matter's reports are listed in the matter)."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                f"SELECT {_REPORT_COLS} FROM verification_reports WHERE user_id = :u "
                "ORDER BY created_at DESC, id LIMIT 200"
            ),
            {"u": p.user_id},
        ).all()
    return [_report(r) for r in rows]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> ReportOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        r = conn.execute(
            text(f"SELECT {_REPORT_COLS} FROM verification_reports WHERE id = :id"),
            {"id": report_id},
        ).one_or_none()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return _report(r)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> Response:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        n = conn.execute(
            text("DELETE FROM verification_reports WHERE id = :id AND user_id = :u"),
            {"id": report_id, "u": p.user_id},
        ).rowcount
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""The key-points sheet of a matter (ADR-028): the matter's own work, verified like an answer.

Order (the same contract as `answer_query`):

1. Material: the matter's conversations (newest first), their case facts, notes and saved sources,
   read under RLS with the user anchor (only a member gets here). Free text: it travels in the USER
   turn only, clipped by `cheatsheet_material_chars`.
2. Sources: ONLY units the matter already cited (message citations and saved sources). Each
   conversation's ids are reloaded under THAT conversation's jurisdiction predicate and embedding
   generation (`hits_by_cite`), after the reform lineage (`carry_forward`: an amended unit is not
   carried silently). Nothing new is searched: the sheet summarises the matter, it does not
   research.
3. No source at all -> a system refusal before any model call (ADR-005).
4. The workflow prompt `hoja_clave` is composed like any other (closed values only); the validator
   checks every citation, every quotation and coverage; legal points need a citation, questions for
   the client and topics to review are marked `[razonamiento estratégico]`; one regeneration, then a
   system-written refusal (ADR-007).
5. Audit row and trace, as for an answer.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from sqlalchemy import Connection, text

from app.chat.service import (
    WORKFLOW_ALIAS,
    ChatDeps,
    Citation,
    jurisdiction_display,
)
from app.corpus.lineage import carry_forward
from app.ingestion.chunker import CHUNKER_VERSION
from app.observability.trace import TracedAttempt, TraceRecord
from app.prompts.composer import Persona, SessionPrompt
from app.rag.context import SessionContext, load_session_context
from app.rag.expansion import build_context, render_context
from app.rag.gate import refusal_block
from app.rag.search import hits_by_cite
from app.rag.types import Fragment, Hit
from app.validation.audit import write_audit
from app.validation.loop import generate_validated
from app.validation.types import RetrievedSet, ValidationConfig

WORKFLOW: Final = "hoja_clave"
NO_MATERIAL = "retrieval.no_matter_sources"  # refusal reason key (the UI translates it)
_CITE_MARK = re.compile(r"\s*\[\s*cite\s*:[^\]]*\]", re.IGNORECASE)
_PROMPT_LANG = {"es": "es-MX", "en": "en-US"}
_LAB: dict[str, dict[str, Any]] = {
    "es": {"matter": "Asunto", "ref": "Referencia", "desc": "Descripción",
           "notes": "Notas del asunto", "conv": "Conversación",
           "kind": {"chat": "consulta", "research": "investigación", "case": "caso"},
           "side": "Parte que represento", "facts": "Hechos", "claim": "Pretensión",
           "user": "Usuario", "assistant": "Asistente", "saved": "Fuentes guardadas en el asunto",
           "material": "Material del asunto (no es fuente)",
           "now": "Mensaje actual:\nPrepara la hoja de puntos clave de este asunto."},
    "en": {"matter": "Matter", "ref": "Reference", "desc": "Description", "notes": "Matter notes",
           "conv": "Conversation", "kind": {"chat": "chat", "research": "research", "case": "case"},
           "side": "Side I represent", "facts": "Facts", "claim": "Claim", "user": "User",
           "assistant": "Assistant", "saved": "Sources saved in the matter",
           "material": "Matter material (not a source)",
           "now": "Current message:\nPrepare the key-points sheet of this matter."},
}  # fmt: skip
_SIDE = {
    "es": {"actor": "parte actora", "demandado": "parte demandada", "defensa": "defensa",
           "acusacion": "acusación", "tercero": "tercero interesado"},
    "en": {"actor": "plaintiff", "demandado": "defendant", "defensa": "defense",
           "acusacion": "prosecution", "tercero": "interested third party"},
}  # fmt: skip


class MatterNotFoundError(LookupError):
    """The matter does not exist or the caller is not a member (RLS hides both the same way)."""


@dataclass
class Material:
    user_text: str
    persona: Persona
    contexts: list[tuple[SessionContext, list[str]]]  # (conversation scope, ids it cited)
    saved: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    cite_weight: Counter[str] = field(default_factory=Counter)  # how often the matter cited an id


@dataclass(frozen=True)
class CheatsheetResult:
    request_id: uuid.UUID
    status: Literal["answered", "refused"]
    content: str | None
    refusal_reason: str | None
    citations: tuple[Citation, ...]
    material: dict[str, int]


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split()) if "\n" not in s else s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def collect(
    conn: Connection, tenant_id: uuid.UUID, matter_id: uuid.UUID, language: Literal["es", "en"],
    deps: ChatDeps,
) -> Material:  # fmt: skip
    s = deps.settings
    lab = _LAB[language]
    m = conn.execute(
        text("SELECT title, client_ref, description FROM matters WHERE id = :m"), {"m": matter_id}
    ).one_or_none()
    if m is None:
        raise MatterNotFoundError(str(matter_id))
    convs = conn.execute(
        text(
            "SELECT id, kind, title, language, jurisdiction_id, persona, include_historical, "
            "case_side, case_facts, case_claim FROM conversations WHERE matter_id = :m "
            "ORDER BY updated_at DESC, id LIMIT :n"
        ),
        {"m": matter_id, "n": s.cheatsheet_conversations_max},
    ).all()
    notes = (
        conn.execute(
            text(
                "SELECT body FROM matter_notes WHERE matter_id = :m "
                "ORDER BY created_at DESC LIMIT 20"
            ),
            {"m": matter_id},
        )
        .scalars()
        .all()
    )
    saved = list(
        conn.execute(
            text("SELECT cite_id FROM matter_sources WHERE matter_id = :m ORDER BY added_at"),
            {"m": matter_id},
        ).scalars()
    )
    head = [f"{lab['matter']}: {_clip(m.title, 200)}"]
    if m.client_ref:
        head.append(f"{lab['ref']}: {_clip(m.client_ref, 200)}")
    if m.description:
        head.append(f"{lab['desc']}: {_clip(m.description, 1500)}")
    if notes:
        head.append(lab["notes"] + ":\n" + "\n".join(f"- {_clip(n, 800)}" for n in notes))
    blocks: list[str] = []
    contexts: list[tuple[SessionContext, list[str]]] = []
    weight: Counter[str] = Counter(saved)
    personas: Counter[str] = Counter()
    turns = 2 * s.cheatsheet_turns_per_conversation
    for c in convs:
        msgs = conn.execute(
            text(
                "SELECT role, content, status, citations FROM messages WHERE conversation_id = :c "
                "ORDER BY seq"
            ),
            {"c": c.id},
        ).all()
        ids = list(dict.fromkeys(x["cite_id"] for mm in msgs for x in (mm.citations or [])))
        weight.update(x["cite_id"] for mm in msgs for x in (mm.citations or []))
        personas[c.persona] += 1
        ctx = load_session_context(
            conn,
            tenant_id,
            c.jurisdiction_id,
            language=c.language,
            include_historical=c.include_historical,
            matter_id=matter_id,
        )
        contexts.append((ctx, list(carry_forward(conn, tuple(ids)))))
        lines = [f"{lab['conv']} «{_clip(c.title, 200)}» ({lab['kind'][c.kind]})"]
        if c.kind == "case" and c.case_side:
            lines.append(f"{lab['side']}: {_SIDE[language][c.case_side]}")
            lines.append(f"{lab['facts']}: {_clip(c.case_facts or '', 3000)}")
            if c.case_claim:
                lines.append(f"{lab['claim']}: {_clip(c.case_claim, 1500)}")
        for mm in msgs[-turns:]:
            if mm.role == "assistant" and mm.status != "answered":
                continue  # a refusal is no material
            # earlier answers' citation markers are dropped: the material is not a source, and a
            # citation copied from an old answer would skip finding its support in the fragments
            body = (
                _CITE_MARK.sub("", mm.content) if s.cheatsheet_strip_material_cites else mm.content
            )
            lines.append(f"{lab[mm.role]}: {_clip(body, 1500)}")
        blocks.append("\n".join(lines))
    if saved:
        head.append(f"{lab['saved']}: {len(saved)}")
    budget = s.cheatsheet_material_chars
    kept: list[str] = []
    for b in ["\n".join(head), *blocks]:  # the matter first, then the newest conversations
        if len(b) > budget:
            if not kept:
                kept.append(b[:budget])
            break
        kept.append(b)
        budget -= len(b)
    persona = personas.most_common(1)[0][0] if personas else "general"
    return Material(
        user_text=f"{lab['material']}\n\n" + "\n\n".join(kept) + f"\n\n{lab['now']}",
        persona=persona,  # type: ignore[arg-type]
        contexts=contexts,
        saved=saved,
        counts={
            "conversations": len(convs),
            "notes": len(notes),
            "saved_sources": len(saved),
            "material_chars": sum(len(k) for k in kept),
        },
        cite_weight=weight,
    )


def sources(conn: Connection, mat: Material, deps: ChatDeps) -> list[Hit]:
    """The units the matter cited, each loaded under the scope of a conversation that cited it."""
    by_id: dict[str, Hit] = {}
    for ctx, ids in mat.contexts:
        for h in hits_by_cite(conn, ctx, [i for i in ids if i not in by_id], deps.embedder):
            by_id.setdefault(h.cite_id, h)
    missing = [i for i in carry_forward(conn, tuple(mat.saved)) if i not in by_id]
    for ctx, _ in mat.contexts:  # a saved source has no scope of its own: any of the matter's
        if not missing:
            break
        for h in hits_by_cite(conn, ctx, missing, deps.embedder):
            by_id.setdefault(h.cite_id, h)
        missing = [i for i in missing if i not in by_id]
    ranked = sorted(by_id.values(), key=lambda h: (-mat.cite_weight[h.cite_id], h.cite_id))
    return [
        h.model_copy(update={"rank": i, "score": 1.0})
        for i, h in enumerate(ranked[: deps.settings.cheatsheet_max_sources], 1)
    ]


def _retrieved(frags: list[Fragment], hits: list[Hit]) -> RetrievedSet:
    shown = {cid for f in frags for cid in (f.cite_id, *f.also_cite_ids)}
    entries: list[tuple[str, list[str]]] = [
        (h.cite_id, [h.text]) for h in hits if h.cite_id in shown
    ]
    for f in frags:
        entries.extend((cid, [f.text]) for cid in (f.cite_id, *f.also_cite_ids))
    return RetrievedSet.build(entries)


def _display(conn: Connection, contexts: list[tuple[SessionContext, list[str]]]) -> str:
    seen: list[str] = []
    for ctx, _ in contexts:
        d = jurisdiction_display(conn, ctx)
        if d not in seen:
            seen.append(d)
    label = " · ".join(seen[:3])
    return label if len(label) <= 200 else seen[0]


def build_cheatsheet(
    conn: Connection, tenant_id: uuid.UUID, user_id: uuid.UUID, matter_id: uuid.UUID,
    language: Literal["es", "en"], deps: ChatDeps, on_stage: Callable[[str], None] | None = None,
) -> CheatsheetResult:  # fmt: skip
    """`conn` is inside `tenant_transaction(tenant_id, user_id=...)`."""
    anchored = conn.execute(text("SELECT app_tenant_id()")).scalar_one()
    if anchored != tenant_id:
        raise RuntimeError("connection anchored on another tenant")
    stage = on_stage or (lambda _n: None)
    s = deps.settings
    stage("collecting")
    mat = collect(conn, tenant_id, matter_id, language, deps)
    hits = sources(conn, mat, deps)
    historical = any(ctx.include_historical for ctx, _ in mat.contexts)
    frags = (
        build_context(
            conn,
            hits,
            budget_tokens=s.retrieval_context_budget_tokens,
            counter=deps.counter,
            citable_units=s.context_citable_units,
            include_historical=historical,
            apartado=s.context_unit_apartado,
        )
        if hits
        else []
    )
    material = {**mat.counts, "sources": len(hits)}
    query = f"[hoja de puntos clave] matter={matter_id}"
    filters: dict[str, object] = {"matter_id": str(matter_id), "workflow": WORKFLOW}
    hit_ids = [h.cite_id for h in hits]
    if not frags:  # nothing the matter cited can be shown: no model call (ADR-005)
        block = refusal_block(NO_MATERIAL)
        rid = write_audit(
            conn,
            tenant_id=tenant_id,
            query_text=query,
            filters=filters,
            retrieved_cite_ids=hit_ids,
            result=None,
            output_text=block,
            event_type="refusal",
            user_id=user_id,
            extra_verdict={
                "refusal_reason": NO_MATERIAL,
                "model_profile": deps.model_profile,
                "workflow": WORKFLOW,
                "material": material,
            },
        )
        return CheatsheetResult(rid, "refused", None, NO_MATERIAL, (), material)

    stage("generating")
    alias = WORKFLOW_ALIAS[WORKFLOW]
    session = SessionPrompt(
        workflow=WORKFLOW,
        persona=mat.persona,
        language=_PROMPT_LANG[language],  # type: ignore[arg-type]
        jurisdiction_display=_display(conn, mat.contexts),
    )
    context = render_context(frags)
    prompt = deps.composer.compose(session, context)
    spec = (
        deps.aliases.require_pinned(alias)
        if deps.require_pinned_models
        else deps.aliases.spec(alias)
    )
    retrieved = _retrieved(frags, hits)
    result = generate_validated(
        deps.llm,
        alias,
        system=prompt.system,
        user=mat.user_text,
        retrieved=retrieved,
        workflow=WORKFLOW,
        tenant_id=str(tenant_id),
        max_tokens=spec.max_answer_tokens,
        regen_max_tokens=s.validator_regen_max_tokens,
        config=ValidationConfig.from_settings(s),
    )
    answered = result.answer is not None
    reason = None if answered else (result.refusal.reason_key if result.refusal else "refused")
    output = result.answer if answered else (result.refusal.block if result.refusal else None)
    rid = write_audit(
        conn,
        tenant_id=tenant_id,
        query_text=query,
        filters=filters,
        retrieved_cite_ids=hit_ids,
        result=result,
        output_text=output,
        event_type="query" if answered else "refusal",
        model_alias=alias,
        user_id=user_id,
        extra_verdict={
            "model_profile": deps.model_profile,
            "workflow": WORKFLOW,
            "material": material,
        },
    )
    deps.tracer.record(
        TraceRecord(
            request_id=rid,
            tenant_id=tenant_id,
            occurred_at=datetime.now(UTC),
            query=query,
            session={
                "language": language,
                "prompt_language": _PROMPT_LANG[language],
                "persona": mat.persona,
                "workflow": WORKFLOW,
                "model_profile": deps.model_profile,
            },
            filters_sql="",
            filters_params=dict(filters),
            semantic_ids=(),
            lexical_ids=(),
            hits=(),
            retrieved_texts=dict(retrieved.texts),
            gate={},
            jurisdiction_display=session.jurisdiction_display,
            prompt_versions=prompt.versions,
            prompt_sha256=prompt.sha256,
            prompt_system=prompt.system,
            context=context,
            attempts=tuple(
                TracedAttempt(
                    user=a.user,
                    output=a.output,
                    alias=a.alias,
                    resolved_model_id=a.resolved_model_id,
                    verdict=v,
                )
                for a, v in zip(result.attempts, result.verdicts, strict=True)
            ),
            outcome="answered" if answered else "refused",
            answer=result.answer,
            refusal_block=result.refusal.block if result.refusal else None,
            refusal_reason=reason,
            versions={
                "embedding_model": deps.embedder.model,
                "embedding_revision": deps.embedder.revision,
                "chunker_version": CHUNKER_VERSION,
                **{f"alias:{a}": m for a, m in deps.aliases.pinned_ids().items()},
            },
            validator_config=ValidationConfig.from_settings(s).model_dump(),
        )
    )
    citations: tuple[Citation, ...] = ()
    if answered:
        names = {cid: n for f in frags for cid, n in f.unit_names}
        names.update({h.cite_id: h.citation_string for h in hits})
        names.update({f.cite_id: f.citation_string for f in frags})
        citations = tuple(
            Citation(cite_id=c, citation_string=names[c])
            for c in result.verdicts[-1].cited
            if c in names
        )
    return CheatsheetResult(rid, "answered" if answered else "refused", result.answer, reason,
                            citations, material)  # fmt: skip


def as_row(r: CheatsheetResult) -> dict[str, Any]:
    return {
        "status": r.status,
        "content": r.content,
        "refusal_reason": r.refusal_reason,
        "citations": [c.model_dump() for c in r.citations],
        "material": r.material,
        "request_id": r.request_id,
    }


__all__ = ["CheatsheetResult", "MatterNotFoundError", "build_cheatsheet", "collect", "sources"]

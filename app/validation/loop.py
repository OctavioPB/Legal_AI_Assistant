"""Generate -> validate -> (one regeneration) -> answer or system-written refusal (WS5.T02).

Every refusal here is composed by code from the fixed `<insuficiente>` block and a catalog key; the
model's own refusal text is discarded, never shown (ADR-007, invariant 6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.orchestration.aliases import AliasName
from app.orchestration.client import LLMClient
from app.rag.gate import refusal_block
from app.validation.types import (
    RefusalReason,
    RetrievedSet,
    ValidationConfig,
    ValidationRefusal,
    Verdict,
)
from app.validation.validator import normalize_gaps, validate


class Attempt(BaseModel):
    """One model call, kept whole so a query can be reconstructed from its trace."""

    model_config = ConfigDict(frozen=True)

    user: str  # the user turn actually sent (original query, plus code-written feedback on a retry)
    output: str  # raw model text, before any discarding
    alias: AliasName  # the alias that answered (a fallback differs from the requested one)
    resolved_model_id: str


class ValidatedAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str | None  # the model's text, only when grounded
    refusal: ValidationRefusal | None
    verdicts: tuple[Verdict, ...]  # one per attempt, in order: all of them go to the audit log
    regenerations: int
    resolved_model_id: str
    attempts: tuple[Attempt, ...] = ()


MAX_PREVIOUS_CHARS = 16_000


def build_feedback(v: Verdict) -> str:
    """Correction instructions written by code from the verdict (never by a model)."""
    lines = ["Tu respuesta anterior no cumplió las reglas. Corrige SOLO lo que se indica:"]
    if v.invalid:
        lines.append(f"- Citaste ids que no están en los fragmentos: {', '.join(v.invalid)}.")
    if v.placeholders:
        lines.append(f"- Copiaste marcadores de ejemplo como cita: {', '.join(v.placeholders)}.")
    if v.uncovered:
        lines.append("- Estas oraciones afirman contenido jurídico sin cita ni marca:")
        lines.extend(f'    "{s}"' for s in v.uncovered[:5])
    if v.bad_quotes:
        lines.append(
            "- Estos textos entrecomillados no aparecen literalmente en el fragmento citado:"
        )
        lines.extend(f'    "{s}"' for s in v.bad_quotes[:5])
    if not v.cited and not v.invalid and not v.placeholders:
        lines.append("- No incluiste ninguna cita válida.")
    lines.append("Responde con la respuesta completa, conservando igual todo lo que no se señala.")
    lines.append("Usa solo los ids de los fragmentos y copia literalmente lo que entrecomilles.")
    return "\n".join(lines)


def _refusal(reason: RefusalReason, verdicts: list[Verdict]) -> ValidationRefusal:
    return ValidationRefusal(
        reason_key=reason, block=refusal_block(reason), verdicts=tuple(verdicts)
    )


def generate_validated(
    llm: LLMClient,
    alias: AliasName,
    *,
    system: str,
    user: str,
    retrieved: RetrievedSet,
    workflow: str,
    tenant_id: str,
    max_tokens: int,
    regen_max_tokens: int,
    config: ValidationConfig | None = None,
) -> ValidatedAnswer:
    cfg = config or ValidationConfig()
    verdicts: list[Verdict] = []
    attempts: list[Attempt] = []
    current_user = user
    model_id = ""
    for attempt in range(cfg.max_retries + 1):
        result = llm.complete(
            alias,
            system=system,
            user=current_user,
            max_tokens=max_tokens if attempt == 0 else regen_max_tokens,
            tenant_id=tenant_id,
        )
        model_id = result.resolved_model_id
        v = validate(result.text, retrieved, workflow=workflow, config=cfg)
        verdicts.append(v)
        attempts.append(
            Attempt(
                user=current_user,
                output=result.text,
                alias=result.alias,
                resolved_model_id=result.resolved_model_id,
            )
        )
        if v.refused:
            return ValidatedAnswer(
                answer=None,
                refusal=_refusal("validation.model_declined", verdicts),
                verdicts=tuple(verdicts),
                attempts=tuple(attempts),
                regenerations=attempt,
                resolved_model_id=model_id,
            )
        if v.prose_refusal:  # refusal without the block: discarded, the system emits the block
            return ValidatedAnswer(
                answer=None,
                refusal=_refusal("validation.format_violation", verdicts),
                verdicts=tuple(verdicts),
                attempts=tuple(attempts),
                regenerations=attempt,
                resolved_model_id=model_id,
            )
        if v.grounded:
            return ValidatedAnswer(
                answer=normalize_gaps(result.text)[0],
                refusal=None,
                verdicts=tuple(verdicts),
                attempts=tuple(attempts),
                regenerations=attempt,
                resolved_model_id=model_id,
            )
        # The call is single-turn: without the previous answer in front of it, "fix it" makes the
        # model rewrite from scratch, drift and fail on something else (seen with the real model).
        previous = result.text[:MAX_PREVIOUS_CHARS]
        current_user = (
            f"{user}\n\n--- Respuesta anterior (tiene errores) ---\n{previous}\n\n"
            f"--- Corrección requerida ---\n{build_feedback(v)}"
        )
    return ValidatedAnswer(
        answer=None,
        refusal=_refusal("validation.unsupported", verdicts),
        verdicts=tuple(verdicts),
        attempts=tuple(attempts),
        regenerations=cfg.max_retries,
        resolved_model_id=model_id,
    )

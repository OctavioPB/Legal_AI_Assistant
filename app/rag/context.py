"""SessionContext: the ONLY source of tenant/jurisdiction/language for a query (jurisdictions.md).

It is built from a catalog row selected by id, never from free text. Rewrites, translations and
follow-up planning (ADR-009) cannot produce or change it: it is frozen.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

Level = Literal["federal", "state", "municipal"]


class UnknownJurisdictionError(ValueError):
    """The selected jurisdiction id is not in the closed catalog."""


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: uuid.UUID
    jurisdiction_id: int
    country: Literal["MX", "US"]
    level: Level
    state_code: str | None = None
    municipality: str | None = None
    language: Literal["es", "en"] = "es"
    include_historical: bool = False  # temporal queries set this explicitly (jurisdictions.md)


def load_session_context(
    conn: Connection,
    tenant_id: uuid.UUID,
    jurisdiction_id: int,
    *,
    language: Literal["es", "en"] = "es",
    include_historical: bool = False,
) -> SessionContext:
    row = conn.execute(
        text("SELECT country, level, state_code, municipality FROM jurisdictions WHERE id = :i"),
        {"i": jurisdiction_id},
    ).first()
    if row is None:
        raise UnknownJurisdictionError(f"jurisdiction {jurisdiction_id} is not in the catalog")
    return SessionContext(
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction_id,
        country=row.country,
        level=row.level,
        state_code=row.state_code,
        municipality=row.municipality,
        language=language,
        include_historical=include_historical,
    )

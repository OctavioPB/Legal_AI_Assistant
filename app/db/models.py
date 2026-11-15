"""SQLAlchemy models. The migrations are the source of truth for DDL (RLS, triggers, generated
columns); these models are the typed access layer and never call `create_all`.

Columns derived by DB triggers (chunks.country/level/..., see migration 0002) are declared so they
can be read, and are filled by the database on INSERT.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

EMBEDDING_DIM = 1024


class LTree(UserDefinedType[str]):
    """PostgreSQL ltree, handled as a dotted string."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ltree"


class Base(DeclarativeBase):
    pass


class Jurisdiction(Base):
    __tablename__ = "jurisdictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    name_es: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)


class Persona(Base):
    __tablename__ = "personas"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    label_es: Mapped[str] = mapped_column(Text)
    label_en: Mapped[str] = mapped_column(Text)
    legal_domain: Mapped[list[str]] = mapped_column(ARRAY(Text))


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text, server_default="pooled")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("jurisdictions.id"))
    doc_type: Mapped[str] = mapped_column(Text)
    legal_domain: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    title: Mapped[str] = mapped_column(Text)
    official_id: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    version_label: Mapped[str | None] = mapped_column(Text)
    source_uri: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str] = mapped_column(Text)
    # ADR-025: a client file of a matter (doc_nodes/chunks derive theirs by trigger)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocNode(Base):
    __tablename__ = "doc_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    node_type: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(LTree())
    full_text: Mapped[str] = mapped_column(Text)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    # Derived by chunks_derive_columns_trg from the document + catalog; read-only in practice.
    jurisdiction_id: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    country: Mapped[str] = mapped_column(Text, server_default="")
    level: Mapped[str] = mapped_column(Text, server_default="")
    state_code: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text, server_default="")
    legal_domain: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    language: Mapped[str] = mapped_column(Text, server_default="")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_navigation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cite_id: Mapped[str] = mapped_column(Text)
    citation_string: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    breadcrumb: Mapped[str] = mapped_column(Text)
    embedded_text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_revision: Mapped[str] = mapped_column(Text)
    chunker_version: Mapped[int] = mapped_column(Integer)
    chunk_thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fts: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("(see migration 0002)", persisted=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="member")
    preferred_language: Mapped[str] = mapped_column(Text, server_default="es-MX")
    password_hash: Mapped[str | None] = mapped_column(Text)  # scrypt string, see app/api/auth.py
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    """A SessionContext persisted: catalog keys only, no free text."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str] = mapped_column(Text)
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("jurisdictions.id"))
    persona: Mapped[str] = mapped_column(ForeignKey("personas.code"))
    workflow: Mapped[str] = mapped_column(Text)
    include_historical: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dossier(Base):
    __tablename__ = "dossiers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    workflow: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only (privileges + trigger); the app role can only SELECT and INSERT."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), server_default=text("gen_random_uuid()")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(Text)
    query_text: Mapped[str | None] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    retrieved_cite_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    output_text: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    model_alias: Mapped[str | None] = mapped_column(String)
    resolved_model_id: Mapped[str | None] = mapped_column(String)
    regenerations: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))

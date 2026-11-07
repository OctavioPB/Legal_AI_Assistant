"""Frozen domain objects of the ingestion layer (layers never exchange loose dicts)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

NodeType = Literal[
    "libro", "titulo", "capitulo", "seccion", "articulo", "fraccion", "inciso", "parrafo",
    "transitorio",
]  # fmt: skip
HEADING_TYPES = frozenset({"libro", "titulo", "capitulo", "seccion"})


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Node(Frozen):
    """One node of the legal tree. Spans index the NORMALIZED source text and cover the node
    including its descendants; `own_end` stops where the first child begins."""

    path: str  # ltree, e.g. "d.l1.t1.a47.fII"
    parent: str | None
    node_type: NodeType
    label: str
    token: str  # cite token, e.g. ART47BIS
    ordinal: int
    start: int
    end: int
    own_end: int
    is_current: bool = True
    is_navigation: bool = False
    flagged: bool = False  # grammar could not type it confidently
    reform_notes: tuple[str, ...] = ()


class ChunkThresholds(Frozen):
    """Stored with every chunk (invariant 5). Measured on `text`; the embedder window is checked
    on the embedded text (breadcrumb + text)."""

    min_tokens: int = 80
    max_tokens: int = 400  # ADR-012 (Proposed): 800 > Cohere v3 window of 512
    embedder_window: int = 512  # embed-multilingual-v3.0, verified 2026-11-06 (docs.cohere.com)
    counter: str = "approx-words-x1.3"

    @model_validator(mode="after")
    def _consistent(self) -> ChunkThresholds:
        if not 0 < self.min_tokens < self.max_tokens <= self.embedder_window:
            raise ValueError("need 0 < min_tokens < max_tokens <= embedder_window")
        return self


class DocMeta(Frozen):
    country: str  # MX | US
    scope: str  # FED | NL | MTY ...
    code: str  # LFT
    title: str
    version_label: str  # 2025.06
    language: Literal["es", "en"] = "es"
    client_file: str | None = None  # set => cite ids use the CF- namespace

    @model_validator(mode="after")
    def _tokens(self) -> DocMeta:
        for v in (self.country, self.scope, self.code):
            if not re.fullmatch(r"[A-Z0-9]+", v):
                raise ValueError(f"cite token must be [A-Z0-9]+: {v!r}")
        if not re.fullmatch(r"[0-9][0-9A-Za-z.]*", self.version_label):
            raise ValueError("version_label must look like 2025.06")
        if self.client_file is not None and not re.fullmatch(r"[A-Z0-9]+", self.client_file):
            raise ValueError("client_file must be [A-Z0-9]+")
        return self


class Chunk(Frozen):
    cite_id: str
    citation_string: str
    node_path: str
    part: int  # 1-based; >1 only for split nodes
    text: str  # EXACT slice of the normalized source
    breadcrumb: str
    embedded_text: str
    char_start: int
    char_end: int
    token_count: int  # of embedded_text
    text_tokens: int
    is_current: bool
    is_navigation: bool
    oversized: bool  # text > max_tokens (single paragraph that cannot be split)
    undersized: bool  # text < min_tokens (whole-node chunk that could not merge up)
    exceeds_window: bool  # embedded_text > embedder window: would be silently truncated

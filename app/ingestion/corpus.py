"""The demo corpus registry (WS2.T07): which file is which law, under which jurisdiction.

The jurisdiction is looked up in the closed catalog by (country, level, state, municipality), never
from free text. `file` paths are relative to data/corpus (gitignored; see MANIFEST.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CorpusDoc:
    key: str
    folder: Literal["text", "raw"]
    country: Literal["MX", "US"]
    scope: str  # cite_id scope segment: FED, NL, MTY, CA
    code: str  # cite_id code segment
    language: Literal["es", "en"]
    doc_type: str  # documents.doc_type
    title: str
    jurisdiction: tuple[
        str, str, str | None, str | None
    ]  # country, level, state_code, municipality
    legal_domain: tuple[str, ...]
    grammar: Literal["mx", "us"] = "mx"
    version_label: str = "2026.11"

    @property
    def filename(self) -> str:
        return f"{self.key}.txt"


CORPUS: tuple[CorpusDoc, ...] = (
    CorpusDoc("LFT", "text", "MX", "FED", "LFT", "es", "ley", "Ley Federal del Trabajo",
              ("MX", "federal", None, None), ("labor",)),
    CorpusDoc("CPEUM", "text", "MX", "FED", "CPEUM", "es", "ley",
              "Constitución Política de los Estados Unidos Mexicanos",
              ("MX", "federal", None, None), ("constitutional",)),
    CorpusDoc("CCF", "text", "MX", "FED", "CCF", "es", "codigo", "Código Civil Federal",
              ("MX", "federal", None, None), ("civil",)),
    CorpusDoc("CPF", "text", "MX", "FED", "CPF", "es", "codigo", "Código Penal Federal",
              ("MX", "federal", None, None), ("criminal",)),
    CorpusDoc("CC_NL", "text", "MX", "NL", "CCNL", "es", "codigo",
              "Código Civil para el Estado de Nuevo León",
              ("MX", "state", "NL", None), ("civil",)),
    CorpusDoc("REG_ADMIN_MTY", "text", "MX", "MTY", "RAPMTY", "es", "reglamento",
              "Reglamento de la Administración Pública Municipal de Monterrey",
              ("MX", "municipal", "NL", "Monterrey"), ("administrative",)),
    CorpusDoc("REG_JUSTICIA_CIVICA_MTY", "text", "MX", "MTY", "RJCMTY", "es", "reglamento",
              "Reglamento de Justicia Cívica del Municipio de Monterrey",
              ("MX", "municipal", "NL", "Monterrey"), ("administrative", "criminal")),
    CorpusDoc("CA_LABOR_CODE", "raw", "US", "CA", "LAB", "en", "statute", "California Labor Code",
              ("US", "state", "CA", None), ("labor",), grammar="us", version_label="2025"),
)  # fmt: skip
BY_KEY = {d.key: d for d in CORPUS}

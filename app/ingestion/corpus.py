"""The demo corpus registry (WS2.T07): which file is which law, under which jurisdiction.

The jurisdiction is looked up in the closed catalog by (country, level, state, municipality), never
from free text. `source_url` is the closed registry of official downloads the reform watcher may
fetch
(ADR-017); nothing else on the network is ever contacted. `file` paths are relative to data/corpus
(gitignored; see MANIFEST.md).
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
    # ADR-017: the official URL the reform watcher checks (closed list, https only). None: the
    # source has no stable official download; a new version is brought to data/corpus/inbox/.
    source_url: str | None = None
    # How `source_url` is read (ADR-026): `file` = the URL IS the law (PDF/text); `leginfo` = the
    # URL is California's official bulk-download directory and the code is extracted from its
    # newest yearly archive by HTTP range requests (`app/ingestion/leginfo.py`).
    fetch: Literal["file", "leginfo"] = "file"
    # ADR-026: approved by an operator from an official index (`corpus_sources`), not in this file.
    # Such a source has no seed: the watcher ingests its first version.
    approved: bool = False

    @property
    def filename(self) -> str:
        return f"{self.key}.txt"


_DIP = "https://www.diputados.gob.mx/LeyesBiblio/pdf/"
# ADR-026 (2026-11-17): the three state/municipal URLs return byte-for-byte the files in
# data/corpus/SHA256SUMS; robots.txt of both hosts allows these paths (a person reviews the terms).
_HCNL = "https://www.hcnl.gob.mx/trabajo_legislativo/leyes/pdf/"
_MTY = "https://www.monterrey.gob.mx/pdf/reglamentos/1/"
LEGINFO = "https://downloads.leginfo.legislature.ca.gov/"

CORPUS: tuple[CorpusDoc, ...] = (
    CorpusDoc("LFT", "text", "MX", "FED", "LFT", "es", "ley", "Ley Federal del Trabajo",
              ("MX", "federal", None, None), ("labor",), source_url=_DIP + "LFT.pdf"),
    CorpusDoc("CPEUM", "text", "MX", "FED", "CPEUM", "es", "ley",
              "Constitución Política de los Estados Unidos Mexicanos",
              ("MX", "federal", None, None), ("constitutional",), source_url=_DIP + "CPEUM.pdf"),
    CorpusDoc("CCF", "text", "MX", "FED", "CCF", "es", "codigo", "Código Civil Federal",
              ("MX", "federal", None, None), ("civil",), source_url=_DIP + "CCF.pdf"),
    CorpusDoc("CPF", "text", "MX", "FED", "CPF", "es", "codigo", "Código Penal Federal",
              ("MX", "federal", None, None), ("criminal",), source_url=_DIP + "CPF.pdf"),
    CorpusDoc("CC_NL", "text", "MX", "NL", "CCNL", "es", "codigo",
              "Código Civil para el Estado de Nuevo León",
              ("MX", "state", "NL", None), ("civil",),
              source_url=_HCNL + "CODIGO%20CIVIL%20PARA%20EL%20ESTADO%20DE%20NUEVO%20LEON.pdf"),
    CorpusDoc("REG_ADMIN_MTY", "text", "MX", "MTY", "RAPMTY", "es", "reglamento",
              "Reglamento de la Administración Pública Municipal de Monterrey",
              ("MX", "municipal", "NL", "Monterrey"), ("administrative",),
              source_url=_MTY + "Reglamento%20de%20la%20Administracion%20Publica%20Municipal"
                                "%20de%20Monterrey.pdf"),
    CorpusDoc("REG_JUSTICIA_CIVICA_MTY", "text", "MX", "MTY", "RJCMTY", "es", "reglamento",
              "Reglamento de Justicia Cívica del Municipio de Monterrey",
              ("MX", "municipal", "NL", "Monterrey"), ("administrative", "criminal"),
              source_url=_MTY + "Reglamento_de_Justicia_Civica_Municipal_de_Monterrey"
                                "_Nuevo_Leon.pdf"),
    CorpusDoc("CA_LABOR_CODE", "raw", "US", "CA", "LAB", "en", "statute", "California Labor Code",
              ("US", "state", "CA", None), ("labor",), grammar="us", version_label="2025",
              source_url=LEGINFO, fetch="leginfo"),
)  # fmt: skip
# The closed catalog of legal domains a source may be filed under: the domains of the corpus and of
# the personas' soft boost (0001_jurisdictions.PERSONAS), plus a few common ones of federal law.
LEGAL_DOMAINS: tuple[str, ...] = (
    "administrative", "civil", "constitutional", "corporate", "criminal", "environmental",
    "family", "finance", "fiscal", "labor", "procedural", "social_security",
)  # fmt: skip
BY_KEY = {d.key: d for d in CORPUS}

"""ADR-031: the legal regime of a cited article, read from the law's own structure, and a check
against the regime of the matter. Warning mode: a mismatch is shown next to the citation, never
used to refuse (until a lawyer reviews the catalog).

Why: in E2E-LAB-01 three delivered answers cited real, verified LFT articles of the wrong regime
for an individual dismissal of a billing assistant: art. 283 Ter (agricultural workers), art. 341
(domestic workers) and art. 436 (collective termination). The validator proves provenance, not
applicability (rag.md); the breadcrumb of each unit already says which regime it belongs to.

Scope: the Ley Federal del Trabajo only, the one law where the regime is written into the
structure. Every other unit has no regime and is never flagged.

* Título Sexto "Trabajos Especiales": each Capítulo is a special regime (Capítulo I,
  "Disposiciones generales", applies to every special regime and is not flagged).
* Títulos Séptimo "Relaciones Colectivas de Trabajo" and Octavo "Huelgas": collective law.
* Everything else (individual relations, working conditions, prescription, procedure) applies to
  every relationship and has no regime.

Not caught on purpose (needs per-article direction tags written by a lawyer): two articles of the
same chapter, e.g. art. 51 (grounds for the WORKER to terminate) cited as the employer's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# The chapter names of Título Sexto as the law writes them (data/corpus/text/LFT.txt, the version
# loaded on 2026-11-07). The stored breadcrumbs lose some of them (a normalizer defect: the name
# is on the line after "CAPITULO IV"), so labels come from here, not from the breadcrumb.
LFT_SPECIAL: dict[str, str] = {
    "II": "Trabajadores de confianza",
    "III": "Trabajadores de los buques",
    "IV": "Trabajo de las tripulaciones aeronáuticas",
    "V": "Trabajo ferrocarrilero",
    "VI": "Trabajo de autotransportes",
    "VII": "Trabajo de maniobras de servicio público en zonas bajo jurisdicción federal",
    "VIII": "Personas Trabajadoras del Campo",
    "IX": "Agentes de comercio y otros semejantes",
    "IX-BIS": "Trabajo en plataformas digitales",
    "X": "Deportistas profesionales",
    "XI": "Personas trabajadoras artistas intérpretes o ejecutantes",
    "XII": "Trabajo a domicilio",
    "XII-BIS": "Teletrabajo",
    "XIII": "Personas Trabajadoras del Hogar",
    "XIII-BIS": "De los Trabajadores en Minas",
    "XIV": "Trabajo en hoteles, restaurantes, bares y otros establecimientos análogos",
    "XV": "Industria familiar",
    "XVI": "Trabajos de Médicos Residentes en Período de Adiestramiento en una Especialidad",
    "XVII": "Trabajo en las Universidades e Instituciones de Educación Superior Autónomas por Ley",
}
ORDINARY = "lft:ordinaria"
COLLECTIVE = "lft:colectivo"
# The closed list a matter may declare (never free text, like jurisdiction).
CASE_REGIMES: tuple[str, ...] = (
    ORDINARY,
    *(f"lft:especial:{k}" for k in LFT_SPECIAL),
    COLLECTIVE,
)

Kind = Literal["special", "special_general", "collective"]
Reason = Literal["special_regime", "collective_regime"]

_LFT = "MX-FED-LFT-"
_SEP = " › "
_TITULO = re.compile(r"^T[IÍ]TULO\s+(SEXTO|S[EÉ]PTIMO|OCTAVO)\b", re.IGNORECASE)
_CAPITULO = re.compile(r"^CAP[IÍ]TULO\s+([IVXLC]+)(?:\s+(BIS|TER))?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Regime:
    code: str  # "lft:especial:VIII", "lft:especial:general", "lft:colectivo"
    kind: Kind
    label: str  # the law's own heading


def regime_of(cite_id: str, breadcrumb: str) -> Regime | None:
    """The regime of a unit from its breadcrumb, or None (other laws, general provisions)."""
    if not cite_id.startswith(_LFT):
        return None
    parts = [p.strip() for p in breadcrumb.strip().strip("[]").split(_SEP)]
    if len(parts) < 2:
        return None
    titulo = _TITULO.match(parts[1])
    if titulo is None:
        return None
    if titulo.group(1).upper() != "SEXTO":
        return Regime(COLLECTIVE, "collective", parts[1])
    chapter = next((m for p in parts[2:] if (m := _CAPITULO.match(p))), None)
    if chapter is None:
        return None
    key = chapter.group(1).upper() + (f"-{chapter.group(2).upper()}" if chapter.group(2) else "")
    if key == "I":
        return Regime("lft:especial:general", "special_general", "Disposiciones generales")
    label = LFT_SPECIAL.get(key)
    return Regime(f"lft:especial:{key}", "special", label) if label else None


def mismatch(cited: Regime | None, case_regime: str | None) -> Reason | None:
    """Why a cited regime contradicts the matter's, or None. Only clear contradictions: a special
    regime other than the matter's, or collective law in an individual matter."""
    if cited is None or case_regime is None or case_regime == COLLECTIVE:
        return None
    if cited.kind == "collective":
        return "collective_regime"
    if cited.kind == "special" and cited.code != case_regime:
        return "special_regime"
    return None


def case_regime_label(code: str) -> str | None:
    """The law's name for a matter's declared regime (None for ordinary or collective)."""
    if code.startswith("lft:especial:"):
        return LFT_SPECIAL.get(code.removeprefix("lft:especial:"))
    return None

"""ADR-031: the legal regime of a cited LFT unit, from the law's structure, against the matter's.

The evidence test uses the REAL stored breadcrumbs (local corpus, 2026-11-26) of every unit cited
by the eight delivered answers of E2E-LAB-01, an individual dismissal of a billing assistant. The
technical team's reading found five answers with a citation that does not govern the case; three
of them are a regime contradiction, and the check must flag exactly those three and nothing else.
"""

from __future__ import annotations

import pytest

from app.corpus.regimes import CASE_REGIMES, LFT_SPECIAL, case_regime_label, mismatch, regime_of
from tests.test_client_files import firm  # noqa: F401 - the two-firm API fixture

LAW = "Ley Federal del Trabajo"
E2E_CITED = {  # cite_id -> breadcrumb, as stored
    "CF-D0E3155931F0-S9": "[Contrato individual de trabajo › SÉPTIMA.- VACACIONES Y PRESTACIONES]",
    "MX-FED-CPEUM-ART123-APARTADOA-FRACV-v2026.11": "[Constitución Política de los Estados Unidos "
    "Mexicanos › Título Sexto Del Trabajo y de la Previsión Social › Artículo 123 › Apartado A › "
    "Fracción V]",
    "MX-FED-LFT-ART133-FRACXV-v2026.11": f"[{LAW} › TITULO CUARTO › CAPITULO I › Artículo 133 › "
    "Fracción XV]",
    "MX-FED-LFT-ART162-v2026.11": f"[{LAW} › TITULO CUARTO › CAPITULO IV › Artículo 162]",
    "MX-FED-LFT-ART283TER-v2026.11": f"[{LAW} › TITULO SEXTO Trabajos Especiales › CAPITULO VIII "
    "Personas Trabajadoras del Campo › Artículo 283 Ter]",
    "MX-FED-LFT-ART341-v2026.11": f"[{LAW} › TITULO SEXTO Trabajos Especiales › CAPITULO XIII "
    "Personas Trabajadoras del Hogar › Artículo 341]",
    "MX-FED-LFT-ART436-v2026.11": f"[{LAW} › TITULO SEPTIMO Relaciones Colectivas de Trabajo › "
    "CAPITULO VIII › Artículo 436]",
    "MX-FED-LFT-ART47-FRACII-v2026.11": f"[{LAW} › TITULO SEGUNDO Relaciones Individuales de "
    "Trabajo › CAPITULO IV › Artículo 47 › Fracción II]",
    "MX-FED-LFT-ART48-v2026.11": f"[{LAW} › TITULO SEGUNDO Relaciones Individuales de Trabajo › "
    "CAPITULO IV › Artículo 48]",
    "MX-FED-LFT-ART50-v2026.11": f"[{LAW} › TITULO SEGUNDO Relaciones Individuales de Trabajo › "
    "CAPITULO IV › Artículo 50]",
    "MX-FED-LFT-ART519-v2026.11": f"[{LAW} › TITULO DECIMO Prescripción › Artículo 519]",
    "MX-FED-LFT-ART51-v2026.11": f"[{LAW} › TITULO SEGUNDO Relaciones Individuales de Trabajo › "
    "CAPITULO IV › Artículo 51]",
    "MX-FED-LFT-ART684B-v2026.11": f"[{LAW} › TITULO DECIMO Prescripción › CAPITULO I › Artículo "
    "684 B]",
    "MX-FED-LFT-ART685TER-v2026.11": f"[{LAW} › TITULO DECIMO Prescripción › CAPITULO I Principios "
    "Procesales › Artículo 685 Ter]",
    "MX-FED-LFT-ART76-v2026.11": f"[{LAW} › TITULO TERCERO Condiciones de Trabajo › CAPITULO IV › "
    "Artículo 76]",
    "MX-FED-LFT-ART79-v2026.11": f"[{LAW} › TITULO TERCERO Condiciones de Trabajo › CAPITULO IV › "
    "Artículo 79]",
    "MX-FED-LFT-ART87-v2026.11": f"[{LAW} › TITULO TERCERO Condiciones de Trabajo › CAPITULO V "
    "Salario › Artículo 87]",
}


def test_e2e_lab01_flags_exactly_the_three_regime_contradictions() -> None:
    flagged = {
        cid: reason
        for cid, crumb in E2E_CITED.items()
        if (reason := mismatch(regime_of(cid, crumb), "lft:ordinaria"))
    }
    assert flagged == {
        "MX-FED-LFT-ART283TER-v2026.11": "special_regime",
        "MX-FED-LFT-ART341-v2026.11": "special_regime",
        "MX-FED-LFT-ART436-v2026.11": "collective_regime",
    }


def test_the_regime_comes_from_titulo_and_capitulo_with_the_laws_own_names() -> None:
    campo = regime_of("MX-FED-LFT-ART283TER-v2026.11", E2E_CITED["MX-FED-LFT-ART283TER-v2026.11"])
    assert campo is not None and (campo.code, campo.kind, campo.label) == (
        "lft:especial:VIII", "special", "Personas Trabajadoras del Campo",
    )  # fmt: skip
    # a chapter whose name the stored breadcrumb lost still gets the law's name
    air = regime_of("MX-FED-LFT-ART215-v2026.11", f"[{LAW} › TITULO SEXTO Trabajos Especiales › "
                    "CAPITULO IV › Artículo 215]")  # fmt: skip
    assert air is not None and air.label == "Trabajo de las tripulaciones aeronáuticas"
    bis = regime_of("MX-FED-LFT-ART343A-v1", f"[{LAW} › TITULO SEXTO Trabajos Especiales › "
                    "Capitulo XIII Bis De Los Trabajadores en Minas › Artículo 343-A]")  # fmt: skip
    assert bis is not None and bis.code == "lft:especial:XIII-BIS"
    strike = regime_of("MX-FED-LFT-ART440-v1", f"[{LAW} › TITULO OCTAVO Huelgas › CAPITULO I › "
                       "Artículo 440]")  # fmt: skip
    assert strike is not None and strike.kind == "collective"
    general = regime_of("MX-FED-LFT-ART181-v1", f"[{LAW} › TITULO SEXTO Trabajos Especiales › "
                        "CAPITULO I › Artículo 181]")  # fmt: skip
    assert general is not None and general.kind == "special_general"


@pytest.mark.parametrize(
    ("case", "cited", "expected"),
    [
        (None, "lft:especial:VIII", None),  # nothing declared: never a warning
        ("lft:ordinaria", None, None),  # a general provision applies to everyone
        ("lft:especial:VIII", "lft:especial:VIII", None),  # the matter's own regime
        ("lft:especial:VIII", "lft:especial:XIII", "special_regime"),
        ("lft:especial:VIII", "lft:colectivo", "collective_regime"),
        ("lft:colectivo", "lft:especial:XIII", None),  # collective matters: not judged
        ("lft:ordinaria", "lft:especial:general", None),  # Capítulo I applies to every regime
    ],
)
def test_only_clear_contradictions_are_flagged(
    case: str | None, cited: str | None, expected: str | None
) -> None:
    from app.corpus.regimes import Regime

    kinds = {"lft:colectivo": "collective", "lft:especial:general": "special_general"}
    reg = Regime(cited, kinds.get(cited, "special"), "x") if cited else None  # type: ignore[arg-type]
    assert mismatch(reg, case) == expected


def test_other_laws_never_get_a_regime_even_with_the_same_heading_words() -> None:
    cpeum = "MX-FED-CPEUM-ART123-APARTADOA-FRACV-v2026.11"
    assert regime_of(cpeum, E2E_CITED[cpeum]) is None  # "Título Sexto" of the Constitution
    assert regime_of("CF-D0E3155931F0-S9", E2E_CITED["CF-D0E3155931F0-S9"]) is None


def test_the_case_catalog_is_closed_and_labelled_from_the_law() -> None:
    assert CASE_REGIMES[0] == "lft:ordinaria" and CASE_REGIMES[-1] == "lft:colectivo"
    assert len(CASE_REGIMES) == len(LFT_SPECIAL) + 2
    assert case_regime_label("lft:especial:XIII") == "Personas Trabajadoras del Hogar"
    assert case_regime_label("lft:ordinaria") is None


# ── citations carry the regime and the warning ──────────────────────────────────────────────────
def _hit(cite_id: str, crumb: str):  # type: ignore[no-untyped-def]
    import uuid

    from app.rag.types import Hit

    u = uuid.uuid4()
    return Hit(chunk_id=u, cite_id=cite_id, citation_string=cite_id, document_id=u, node_id=u,
               text="t", breadcrumb=crumb, score=0.9, rank=1)  # fmt: skip


def test_citations_of_an_answer_carry_their_regime_and_the_matters_warning() -> None:
    from app.chat.service import _citations
    from app.rag.pipeline import Retrieval
    from app.rag.types import Fragment

    campo, ordinary = "MX-FED-LFT-ART283TER-v2026.11", "MX-FED-LFT-ART48-v2026.11"
    unit = "MX-FED-LFT-ART283TER-FRACI-v2026.11"  # rendered inside the expanded article
    r = Retrieval(
        refusal=None, semantic_ids=(), lexical_ids=(),
        hits=(_hit(campo, E2E_CITED[campo]), _hit(ordinary, E2E_CITED[ordinary])),
        fragments=(Fragment(cite_id=campo, also_cite_ids=(unit,), citation_string=campo, text="t",
                            expanded=True, tokens=1, unit_names=((unit, "art. 283 Ter, fracc. I"),)),),
    )  # fmt: skip
    cited = (campo, ordinary, unit)
    plain = {c.cite_id: c for c in _citations(r, cited)}
    assert plain[campo].regime == "lft:especial:VIII" and plain[campo].applicability is None
    assert plain[unit].regime == "lft:especial:VIII"  # takes its article's breadcrumb
    assert plain[ordinary].regime is None
    warned = {c.cite_id: c.applicability for c in _citations(r, cited, "lft:ordinaria")}
    assert warned == {campo: "special_regime", ordinary: None, unit: "special_regime"}
    # a value outside the closed list is ignored, never trusted
    assert all(c.applicability is None for c in _citations(r, cited, "lft:inventado"))


# ── the matter declares its regime from the closed list ─────────────────────────────────────────
def test_a_matter_declares_its_regime_from_the_closed_list(firm) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from tests.test_api import auth

    client, *_ = firm
    ana = auth(client)
    regimes = client.get("/matters/regimes", headers=ana).json()
    assert [r["code"] for r in regimes] == list(CASE_REGIMES)
    assert {r["code"]: r["label"] for r in regimes}["lft:especial:VIII"] == (
        "Personas Trabajadoras del Campo"
    )
    bad = client.post("/matters", json={"title": "x", "labor_regime": "lft:otra"}, headers=ana)
    assert bad.status_code == 422
    m = client.post("/matters", json={"title": "Despido", "labor_regime": "lft:ordinaria"},
                    headers=ana).json()  # fmt: skip
    assert m["labor_regime"] == "lft:ordinaria"
    kept = client.patch(f"/matters/{m['id']}", json={"title": "Despido injustificado"},
                        headers=ana).json()  # fmt: skip
    assert kept["labor_regime"] == "lft:ordinaria"  # not sent: unchanged
    cleared = client.patch(f"/matters/{m['id']}", json={"labor_regime": ""}, headers=ana).json()
    assert cleared["labor_regime"] is None

"""ADR-051: a sentence that credits a cited decree with a change must match the decree's own list.

Found on golden gd2-024 (2026-11-26): the model wrote that art. 15-C was repealed and cited a
decree (`TRANS4-UNICO`, the 2012 LFT reform) that ADDED it; the decree that repealed it
(`TRANS13-PRIMERO`) was in the same context. The citation existed, so provenance passed.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.validation.decrees import claim, decree_changes, mismatches
from app.validation.loop import build_feedback
from app.validation.types import RetrievedSet
from app.validation.validator import validate
from evals.replay import read, replay

ADDS = (
    "Artículo Único.- Se REFORMAN los artículos 2o; 3o; 4o, fracción I, inciso a); 28; "
    "se ADICIONAN los artículos 3o Bis; 15-A; 15-B; 15-C; 15-D; 22 Bis; 1004-A, y se DEROGAN "
    "los artículos 395; 523, de la Ley Federal del Trabajo, para quedar como sigue:"
)
REPEALS = (
    "Artículo Primero. Se reforman los artículos 12; 13; 14; 15; 127, primer párrafo; 1004-A, "
    "y 1004-C; se adicionan un tercer párrafo del artículo 41; una fracción VIII al artículo 127, "
    "y se derogan los artículos 15A; 15-B; 15-C, y 15-D, de la Ley Federal del Trabajo, para "
    "quedar como sigue:"
)
A, R = "MX-FED-LFT-TRANS4-UNICO-P1-v1", "MX-FED-LFT-TRANS13-PRIMERO-v1"
TEXTS = {A: (ADDS,), R: (REPEALS,)}


def test_a_decree_list_is_read_verb_by_verb() -> None:
    d = decree_changes(REPEALS)
    assert d["repeal"] == {"15A", "15B", "15C", "15D"}
    assert {"12", "15", "127", "1004A", "1004C"} <= d["reform"]
    assert {"41", "127"} <= d["add"]
    a = decree_changes(ADDS)
    assert {"15A", "15C", "3BIS", "22BIS", "1004A"} <= a["add"] and "15C" not in a.get("repeal", ())
    assert {"2", "3", "4", "28"} <= a["reform"] and a["repeal"] == {"395", "523"}


def test_text_that_is_not_a_list_is_not_a_decree() -> None:
    assert decree_changes("Artículo 47.- Son causas de rescisión de la relación de trabajo:") == {}
    assert decree_changes("(Se deroga)") == {}


def test_a_chunk_cut_mid_list_keeps_its_leading_numbers_as_unknown() -> None:
    d = decree_changes("Bis; 424 Bis; 427, con una fracción VII; y se derogan los artículos 395")
    assert {"424BIS", "427"} <= d["unknown"] and d["repeal"] == {"395"}


def test_the_claim_of_a_sentence() -> None:
    verbs, arts = claim("El artículo 15-C fue derogado por el decreto [cite: X].")
    assert verbs == {"repeal"} and arts == {"15C"}
    verbs, arts = claim("Se reformaron los artículos 12, 13 y 1004-A de la Ley.")
    assert verbs == {"reform"} and arts == {"12", "13", "1004A"}


def test_crediting_the_wrong_decree_is_reported_and_the_right_one_passes() -> None:
    wrong = "El artículo 15-C fue derogado [cite: %s]." % A
    right = "El artículo 15-C fue derogado [cite: %s]." % R
    assert mismatches(wrong, [A], TEXTS) == ["15C"]
    assert mismatches(right, [R], TEXTS) == []
    assert mismatches("El artículo 15-C fue adicionado [cite: A].", [A], TEXTS) == []
    # several verbs: one of them must match (here the decree added it, and also "reformó" others)
    assert mismatches("El decreto reformó el art. 28 y adicionó el art. 15-C.", [A], TEXTS) == []


def test_sentences_that_do_not_cite_a_decree_or_claim_no_change_are_not_checked() -> None:
    art = "MX-FED-LFT-ART15C-v1"
    texts = {**TEXTS, art: ("Artículo 15-C.- (Se deroga)",)}
    assert mismatches("El artículo 15-C fue derogado [cite: X].", [art], texts) == []
    assert mismatches("El artículo 15-C trata de la subcontratación.", [A], TEXTS) == []


def test_a_chunk_defers_to_its_expanded_article_list() -> None:
    chunk = "Bis; 15-C; 427; y se derogan los artículos 395"  # verb of 15-C cut off
    texts = {A: (chunk, ADDS)}  # the rendered article holds the whole list: 15-C was ADDED
    assert mismatches("El artículo 15-C fue derogado [cite: A].", [A], texts) == ["15C"]
    assert (
        mismatches("El artículo 15-C fue derogado.", [A], {A: (chunk,)}) == []
    )  # unknown: no proof


def test_the_validator_refuses_to_ground_it_and_the_feedback_names_it() -> None:
    retrieved = RetrievedSet.build([(A, [ADDS]), (R, [REPEALS])])
    bad = validate(
        f"El artículo 15-C fue derogado [cite: {A}].", retrieved, workflow="dossier_builder"
    )
    assert not bad.grounded and bad.problems and bad.decree_mismatches[0].startswith("15C:")
    assert "atribuyen a un decreto citado un cambio" in build_feedback(bad)
    ok = validate(
        f"El artículo 15-C fue derogado [cite: {R}].", retrieved, workflow="dossier_builder"
    )
    assert ok.grounded and ok.decree_mismatches == ()


FIXTURE = Path(__file__).parent / "replay" / "decree_gd2024.jsonl"


def test_real_outputs_of_gd2_024_are_judged_as_a_lawyer_would() -> None:
    """Real attempts (gemma4:26b, 2026-11-26): the generation-2 one credits the 2012 decree with
    the repeal and was accepted then; the generation-1 one cites the repealing decree."""
    records = read(FIXTURE)
    diffs = {d["id"]: d for d in replay(records)}
    wrong = next(r for r in records if "rep-gen2" in r["source"])
    right = next(r for r in records if "rep-gen1" in r["source"])
    assert wrong["recorded"]["grounded"] is True and diffs[wrong["id"]]["now"]["grounded"] is False
    assert right["id"] not in diffs  # unchanged: still grounded
    assert json.dumps(right["output"]).count("TRANS13") >= 1

"""ADR-037 (C): references inside retrieved text become stems of the same or the named law."""

from __future__ import annotations

import uuid

from app.rag.crossref import referenced_stems
from app.rag.types import Hit


def hit(cite_id: str, text: str) -> Hit:
    return Hit(chunk_id=uuid.uuid4(), cite_id=cite_id, citation_string="x", document_id=uuid.uuid4(),
               node_id=uuid.uuid4(), text=text, breadcrumb="", score=1.0, rank=1)  # fmt: skip


def test_an_unnamed_reference_is_the_fragments_own_law_and_a_named_one_its_law() -> None:
    h = hit("MX-FED-LFT-ART48-v2026.11",
            "en los términos del artículo 49 de esta Ley y conforme al artículo 123 de la Constitución")  # fmt: skip
    assert referenced_stems([h], limit=5) == ["MX-FED-LFT-ART49", "MX-FED-CPEUM-ART123"]


def test_the_hits_own_articles_and_repeats_are_left_out_and_the_limit_holds() -> None:
    a = hit(
        "MX-FED-LFT-ART47-FRACII-v1",
        "véase el artículo 47 y el artículo 48; también el artículo 48",
    )
    b = hit("MX-FED-LFT-ART48-v1", "remite al artículo 50, al artículo 51 y al artículo 52")
    assert referenced_stems([a, b], limit=2) == ["MX-FED-LFT-ART50", "MX-FED-LFT-ART51"]


def test_firm_documents_and_us_codes_are_not_expanded() -> None:
    assert referenced_stems([hit("CF-ABC123-S1", "según el artículo 47")], limit=5) == []
    assert referenced_stems([hit("US-CA-LAB-SEC201-v1", "see section 202")], limit=5) == []

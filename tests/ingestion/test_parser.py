from __future__ import annotations

from app.ingestion.chunker import Chunker
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.parser import clean_docling_text
from app.ingestion.tokens import ApproxCounter

DOCLING_SAMPLE = """TÍTULO PRIMERO DISPOSICIONES GENERALES

Artículo 1. El presente Reglamento es de orden público:

- I. Proteger bienes jurídicos:
- a) El bienestar colectivo;
- b) La seguridad de la comunidad;
- II. Establecer mecanismos alternativos.

12

- Una viñeta que no es fracción.
"""


def test_docling_bullets_become_fracciones_and_incisos() -> None:
    cleaned = clean_docling_text(DOCLING_SAMPLE)
    assert "\nI. Proteger" in cleaned and "\na) El bienestar" in cleaned
    assert "- Una viñeta que no es fracción." in cleaned  # real bullets are left alone
    assert "\n12\n" not in cleaned  # isolated page number removed
    src = normalize_text(cleaned)
    nodes = parse(src)
    assert [n.node_type for n in nodes][:5] == [
        "titulo",
        "articulo",
        "fraccion",
        "inciso",
        "inciso",
    ]
    meta = DocMeta(country="MX", scope="NL", code="RJC", title="R", version_label="1")
    assert Chunker(ApproxCounter()).chunk(src, nodes, meta)


HEADER = "CÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN Secretaría General Última Reforma DOF 14-05-2026"
TABLES = f"""Artículo 610.- Texto vigente del artículo.
| I.            | Se deroga.    | Fracción derogada DOF 01-05-2019 |
|---------------|---------------|----------------------------------|
| Artículo 611.- Se deroga. | Artículo 611.- Se deroga. | Artículo derogado DOF 01-05-2019 |
{HEADER}
| Artículo 617.- Se deroga.   | Artículo reformado DOF 30-11-2012. Derogado DOF 01-05-2019 |
Artículo 618.- Texto vigente nuevamente.
"""


def test_tables_become_statute_lines_and_headers_are_dropped() -> None:
    from app.ingestion.parser import clean_docling_text_with_report

    page_noise = "\n".join([HEADER] * 12)  # a header that repeats on every page
    cleaned, removed = clean_docling_text_with_report(TABLES + page_noise)
    assert removed == [HEADER] and HEADER not in cleaned
    lines = cleaned.split("\n")
    assert "I. Se deroga." in lines  # bare marker joined with its text
    assert lines.count("Artículo 611.- Se deroga.") == 1  # duplicated cell collapsed
    assert not any(ln.startswith("|") for ln in lines)


def test_derogated_articles_from_tables_are_typed_and_not_current() -> None:
    from app.ingestion.parser import clean_docling_text

    src = normalize_text(clean_docling_text(TABLES))
    nodes = parse(src)
    by = {n.label: n for n in nodes}
    assert by["Artículo 611"].is_current is False and by["Artículo 617"].is_current is False
    assert by["Artículo 610"].is_current is True and by["Artículo 618"].is_current is True
    assert by["Fracción I"].is_current is False


def test_a_sentence_that_mentions_deroga_is_not_a_derogation() -> None:
    src = normalize_text(
        "Artículo 5.- Es la norma que se deroga por el decreto siguiente en vigor."
    )
    assert parse(src)[0].is_current is True


def test_repeated_annotations_and_list_items_are_content_not_headers() -> None:
    from app.ingestion.parser import clean_docling_text_with_report

    note = "(REFORMADA, P.O. 08 DE ENERO DE 2018)"
    item = "- Personas trabajadoras expuestas a agentes causales de la cédula correspondiente"
    raw = "\n".join(f"Artículo {i}.- Texto.\n{note}\n{item}" for i in range(1, 15))
    cleaned, removed = clean_docling_text_with_report(raw)
    assert removed == [] and cleaned.count(note) == 14 and cleaned.count(item) == 14

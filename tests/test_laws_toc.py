"""The navigator's table of contents names each heading once (ADR-049) and never uses an
article's opening words as a heading."""

from __future__ import annotations

from app.api.laws import _toc_names


def test_a_name_inside_the_label_is_shown_once_as_the_heading() -> None:
    head = "CAPITULO IV\n\nRescisión de las relaciones de trabajo\n\nArtículo 46.- El trabajador..."
    assert _toc_names("capitulo", "CAPITULO IV Rescisión de las relaciones de trabajo", head) == (
        "CAPITULO IV",
        "Rescisión de las relaciones de trabajo",
    )


def test_an_all_caps_name_over_two_lines_is_kept_whole() -> None:
    label = "CAPITULO II DE LAS OBLIGACIONES DEL MANDATARIO CON RESPECTO AL MANDANTE"
    head = "CAPITULO II\n\nDE LAS OBLIGACIONES DEL MANDATARIO CON\n\nRESPECTO AL MANDANTE\n\nArtículo 2562.-"
    assert _toc_names("capitulo", label, head) == (
        "CAPITULO II",
        "DE LAS OBLIGACIONES DEL MANDATARIO CON RESPECTO AL MANDANTE",
    )


def test_an_articles_opening_words_are_never_a_heading() -> None:
    label = "TÍTULO PRIMERO DISPOSICIONES GENERALES"
    head = f"{label}\nArtículo 1. El presente Reglamento es de orden público..."
    assert _toc_names("titulo", label, head) == (label, "")


def test_an_article_keeps_its_first_words_as_its_heading() -> None:
    _, heading = _toc_names("articulo", "Artículo 47", "Artículo 47.- Son causas de rescisión")
    assert heading.startswith("Son causas de rescisión")

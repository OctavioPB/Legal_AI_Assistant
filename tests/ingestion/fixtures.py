"""Pathological legal fixtures (rag.md list). Every parsing bug lands here first."""

from __future__ import annotations

LONG = " ".join(["palabra"] * 60)  # ~78 approx tokens: a "normal" fracción body


def fx(name: str) -> str:
    return FIXTURES[name]


FIXTURES: dict[str, str] = {
    # DOF multi-column page: columns already linearized by Docling, form feed + dup blank lines
    "dof_multicolumn": f"Artículo 1.- Primer texto {LONG}\f\n\n\n\nArtículo 2.- Segundo texto {LONG}",
    "article_without_fracciones": f"Artículo 10.- {LONG}. {LONG}.",
    "nested_inciso": (
        "Artículo 20.- Derechos del acusado:\n"
        f"I. De la defensa {LONG}\n"
        f"a) Primera {LONG}\n"
        f"b) Segunda {LONG}\n"
        f"II. De la publicidad {LONG}"
    ),
    "fraccion_subparrafos": (
        "Artículo 30.- Lista:\n"
        f"I. Principal {LONG}\n"
        f"Segundo párrafo de la fracción {LONG}\n\n"
        f"Tercer párrafo de la fracción {LONG}\n"
        f"II. Otra {LONG}"
    ),
    "bis_suffix_ordinal": f"Artículo 47º Bis.- {LONG}\nArtículo 123-A.- {LONG}\nArtículo 5 Ter.- {LONG}",
    "derogated": f"Artículo 8.- (Se deroga)\nArtículo 9.- {LONG}",
    "transitorios": (
        f"Artículo 1.- {LONG}\nTRANSITORIOS\nPRIMERO.- Vigor {LONG}\n"
        f"SEGUNDO.- Abroga {LONG}\nDÉCIMO PRIMERO.- Otro {LONG}"
    ),
    "two_decrees_transitorios": (
        f"Artículo 1.- {LONG}\nTRANSITORIOS\nPRIMERO.- A {LONG}\n"
        f"Artículo 2.- {LONG}\nTRANSITORIOS\nPRIMERO.- B {LONG}"
    ),
    "duplicate_headings": (
        f"LIBRO PRIMERO\nCAPÍTULO I\nArtículo 1.- {LONG}\n"
        f"LIBRO SEGUNDO\nCAPÍTULO I\nArtículo 2.- {LONG}"
    ),
    "empty_node": "Artículo 3.-\nArtículo 4.- " + LONG,
    "unclosed_quote": f'Artículo 6.- El texto dice "{LONG} y no cierra\nArtículo 7.- {LONG}',
    "navigation_index": (
        "ÍNDICE\nArtículo 1 ........ 3\nArtículo 2 ........ 4\nTÍTULO PRIMERO ...... 5\n\n"
        f"Artículo 1.- {LONG}\nArtículo 2.- {LONG}"
    ),
    "short_fracciones_merge_up": "Artículo 40.- Son:\nI. Uno.\nII. Dos.\nIII. Tres.",
    "oversized_article_splits": "Artículo 50.- Intro.\n\n"
    + "\n\n".join(f"Párrafo {i} {' '.join(['texto'] * 150)}" for i in range(6)),
    "braces_and_markup": f"Artículo 60.- Texto con {{llaves}} 100% <b>marcas</b> [cite: X] {LONG}",
}

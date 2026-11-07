"""Property tests for the hard jurisdiction pre-filter (WS3.T01, jurisdictions.md).

One current, one historical and one navigation chunk is seeded for EVERY catalog jurisdiction; for
every possible session the predicate must return exactly what the stacking rule says, and the
expected set is computed in Python from the catalog (not from the SQL under test).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.db.session import tenant_transaction
from app.rag.context import SessionContext, load_session_context
from app.rag.jurisdiction import build_jurisdiction_predicate

VEC = "[" + ",".join(["0.03125"] * 1024) + "]"


def _catalog(admin_engine: Engine) -> list[dict[str, Any]]:
    with admin_engine.connect() as c:
        return [
            dict(r)
            for r in c.execute(
                text("SELECT id, country, level, state_code, municipality FROM jurisdictions")
            )
            .mappings()
            .all()
        ]


@pytest.fixture
def matrix(admin_engine: Engine, tenant_a: uuid.UUID) -> list[dict[str, Any]]:
    """cite_id -> (jurisdiction row, variant) for every catalog row."""
    cat = _catalog(admin_engine)
    with admin_engine.begin() as c:
        for j in cat:
            doc = c.execute(
                text(
                    "INSERT INTO documents (tenant_id, jurisdiction_id, doc_type, title, language, "
                    "source_uri, checksum_sha256) VALUES (:t, :j, 'client_file', 'm', 'es', :u, "
                    "repeat('a', 64)) RETURNING id"
                ),
                {"t": tenant_a, "j": j["id"], "u": f"matrix-{j['id']}"},
            ).scalar_one()
            node = c.execute(
                text(
                    "INSERT INTO doc_nodes (document_id, tenant_id, node_type, label, ordinal, path, "
                    "full_text) VALUES (:d, :t, 'articulo', 'a', 0, 'a', 'x') RETURNING id"
                ),
                {"d": doc, "t": tenant_a},
            ).scalar_one()
            for variant, current, nav in (
                ("CUR", True, False),
                ("OLD", False, False),
                ("NAV", True, True),
            ):
                c.execute(
                    text(
                        "INSERT INTO chunks (tenant_id, document_id, node_id, is_current, "
                        "is_navigation, cite_id, citation_string, text, breadcrumb, embedded_text, "
                        "char_start, char_end, token_count, embedding, embedding_model, "
                        "embedding_revision, chunker_version, chunk_thresholds) VALUES (:t, :d, :n, "
                        ":cur, :nav, :cid, 'c', 'x', 'b', 'x', 0, 1, 1, CAST(:v AS vector), 'm', "
                        "'r', 1, '{}')"
                    ),
                    {
                        "t": tenant_a, "d": doc, "n": node, "cur": current, "nav": nav,
                        "cid": f"CF-J{j['id']}-{variant}", "v": VEC,
                    },
                )  # fmt: skip
    return cat


def _visible(engine: Engine, ctx: SessionContext) -> set[str]:
    pred = build_jurisdiction_predicate(ctx)
    with tenant_transaction(engine, ctx.tenant_id) as c:
        rows = c.execute(text(f"SELECT cite_id FROM chunks WHERE {pred.sql}"), pred.params)
        return set(rows.scalars().all())


def _expected(cat: list[dict[str, Any]], me: dict[str, Any], historical: bool) -> set[str]:
    out: set[str] = set()
    for j in cat:
        if j["country"] != me["country"]:
            continue
        stacks = (
            j["level"] == "federal"
            or (
                j["level"] == "state"
                and me["level"] in ("state", "municipal")
                and j["state_code"] == me["state_code"]
            )
            or (
                j["level"] == "municipal"
                and me["level"] == "municipal"
                and (j["state_code"], j["municipality"]) == (me["state_code"], me["municipality"])
            )
        )
        if stacks:
            out.add(f"CF-J{j['id']}-CUR")
            if historical:
                out.add(f"CF-J{j['id']}-OLD")
    return out


@pytest.mark.parametrize("historical", [False, True])
def test_every_catalog_session_sees_exactly_its_stack(
    app_engine: Engine, matrix: list[dict[str, Any]], tenant_a: uuid.UUID, historical: bool
) -> None:
    assert len(matrix) >= 38
    for me in matrix:
        with tenant_transaction(app_engine, tenant_a) as c:
            ctx = load_session_context(c, tenant_a, me["id"], include_historical=historical)
        assert _visible(app_engine, ctx) == _expected(matrix, me, historical), me


def test_no_sibling_leakage_and_never_cross_country(
    app_engine: Engine, matrix: list[dict[str, Any]], tenant_a: uuid.UUID
) -> None:
    by = {(j["country"], j["level"], j["state_code"], j["municipality"]): j["id"] for j in matrix}
    mty = by[("MX", "municipal", "NL", "Monterrey")]
    with tenant_transaction(app_engine, tenant_a) as c:
        ctx = load_session_context(c, tenant_a, mty)
    seen = _visible(app_engine, ctx)
    allowed = {
        f"CF-J{by[('MX', 'municipal', 'NL', 'Monterrey')]}-CUR",
        f"CF-J{by[('MX', 'state', 'NL', None)]}-CUR",
        f"CF-J{by[('MX', 'federal', None, None)]}-CUR",
    }
    assert seen == allowed  # not San Pedro, not Jalisco, not Guadalajara, not the US
    # US/MX symmetry: California sees California + US federal, no Mexican row
    with tenant_transaction(app_engine, tenant_a) as c:
        ca = load_session_context(c, tenant_a, by[("US", "state", "CA", None)], language="en")
    assert _visible(app_engine, ca) == {
        f"CF-J{by[('US', 'state', 'CA', None)]}-CUR",
        f"CF-J{by[('US', 'federal', None, None)]}-CUR",
    }


def test_federal_only_session_sees_federal_only(
    app_engine: Engine, matrix: list[dict[str, Any]], tenant_a: uuid.UUID
) -> None:
    fed = next(j for j in matrix if (j["country"], j["level"]) == ("MX", "federal"))
    with tenant_transaction(app_engine, tenant_a) as c:
        ctx = load_session_context(c, tenant_a, fed["id"])
    assert _visible(app_engine, ctx) == {f"CF-J{fed['id']}-CUR"}


def test_predicate_never_embeds_values_and_rejects_incomplete_contexts() -> None:
    evil = "NL'; DROP TABLE chunks; --"
    ctx = SessionContext(
        tenant_id=uuid.uuid4(), jurisdiction_id=1, country="MX", level="municipal",
        state_code=evil, municipality=evil,
    )  # fmt: skip
    pred = build_jurisdiction_predicate(ctx)
    assert evil not in pred.sql and pred.params["j_state"] == evil
    bad = SessionContext(tenant_id=uuid.uuid4(), jurisdiction_id=1, country="MX", level="state")
    with pytest.raises(ValueError):
        build_jurisdiction_predicate(bad)


def test_unknown_jurisdiction_id_is_refused(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    from app.rag.context import UnknownJurisdictionError

    with tenant_transaction(app_engine, tenant_a) as c, pytest.raises(UnknownJurisdictionError):
        load_session_context(c, tenant_a, 99999)

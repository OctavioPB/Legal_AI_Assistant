"""WS1.T01 — the closed jurisdiction catalog: seed content, UNIQUE (NULLS NOT DISTINCT), shape
checks, parent integrity and read-only access for runtime roles."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


def _rows(engine: Engine, where: str = "true") -> list[tuple[str, str, str | None, str | None]]:
    with engine.connect() as conn:
        return [
            tuple(r)  # type: ignore[misc]
            for r in conn.execute(
                text(
                    f"SELECT country, level, state_code, municipality FROM jurisdictions WHERE {where}"
                )
            )
        ]


def test_seed_content(admin_engine: Engine) -> None:
    assert len(_rows(admin_engine, "country='MX' AND level='federal'")) == 1
    states = _rows(admin_engine, "country='MX' AND level='state'")
    assert len(states) == 32
    codes = {s[2] for s in states}
    assert {"NL", "JAL", "CDMX", "MEX", "QROO", "TAMPS"} <= codes
    muni = {(m[2], m[3]) for m in _rows(admin_engine, "level='municipal'")}
    assert muni == {("NL", "Monterrey"), ("NL", "San Pedro Garza García"), ("JAL", "Guadalajara")}
    assert _rows(admin_engine, "country='US'") == [
        ("US", "federal", None, None),
        ("US", "state", "CA", None),
    ] or set(_rows(admin_engine, "country='US'")) == {
        ("US", "federal", None, None),
        ("US", "state", "CA", None),
    }


def test_every_municipality_has_its_state_and_federal(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        orphans = conn.execute(
            text(
                """
                SELECT m.id FROM jurisdictions m
                WHERE m.level = 'municipal' AND NOT EXISTS (
                    SELECT 1 FROM jurisdictions s WHERE s.country = m.country
                      AND s.level = 'state' AND s.state_code = m.state_code)
                UNION ALL
                SELECT s.id FROM jurisdictions s
                WHERE s.level IN ('state','municipal') AND NOT EXISTS (
                    SELECT 1 FROM jurisdictions f WHERE f.country = s.country AND f.level = 'federal')
                """
            )
        ).all()
    assert orphans == []


def test_personas_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        codes = set(conn.execute(text("SELECT code FROM personas")).scalars())
    # corporate_finance is used by golden case gd-0003
    assert {"general", "labor", "criminal", "corporate", "corporate_finance"} <= codes


def test_unique_constraint_covers_null_columns(migrator_engine: Engine) -> None:
    # A plain UNIQUE treats NULLs as distinct and would accept a second 'federal' row.
    with pytest.raises(IntegrityError, match="jurisdictions_natural_key"):
        with migrator_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO jurisdictions (country, level, name_es, name_en) "
                    "VALUES ('MX', 'federal', 'dup', 'dup')"
                )
            )
    with pytest.raises(IntegrityError, match="jurisdictions_natural_key"):
        with migrator_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO jurisdictions (country, level, state_code, name_es, name_en) "
                    "VALUES ('MX', 'state', 'NL', 'dup', 'dup')"
                )
            )


@pytest.mark.parametrize(
    "values",
    [
        "'MX','federal','NL',NULL",  # federal with a state
        "'MX','state',NULL,NULL",  # state without a code
        "'MX','state','NL','Monterrey'",  # state with a municipality
        "'MX','municipal','NL',NULL",  # municipal without a municipality
        "'FR','federal',NULL,NULL",  # country outside the catalog
        "'MX','state','nl',NULL",  # lowercase code
        "'MX','municipal','NL',' Monterrey '",  # untrimmed name
    ],
)
def test_shape_checks_reject_malformed_rows(migrator_engine: Engine, values: str) -> None:
    with pytest.raises(IntegrityError):
        with migrator_engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO jurisdictions (country, level, state_code, municipality, name_es, name_en) "
                    f"VALUES ({values}, 'x', 'x')"
                )
            )


def test_a_municipality_needs_an_existing_state(migrator_engine: Engine) -> None:
    with pytest.raises(DBAPIError, match="no state"):
        with migrator_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO jurisdictions (country, level, state_code, municipality, name_es, name_en) "
                    "VALUES ('US', 'municipal', 'NY', 'Albany', 'x', 'x')"
                )
            )
    with pytest.raises(DBAPIError, match="no state"):  # a US state code does not satisfy MX
        with migrator_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO jurisdictions (country, level, state_code, municipality, name_es, name_en) "
                    "VALUES ('MX', 'municipal', 'CA', 'Tijuana', 'x', 'x')"
                )
            )


def test_runtime_roles_cannot_modify_the_catalog(
    app_engine: Engine, ingestor_engine: Engine, provisioner_engine: Engine
) -> None:
    for engine in (app_engine, ingestor_engine, provisioner_engine):
        with pytest.raises(DBAPIError, match="permission denied"):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO jurisdictions (country, level, name_es, name_en) VALUES ('US', 'federal', 'x', 'x')"
                    )
                )
        with pytest.raises(DBAPIError, match="permission denied"), engine.begin() as conn:
            conn.execute(text("DELETE FROM jurisdictions"))
    with app_engine.begin() as conn:  # but they can read it (the catalog is not tenant data)
        assert conn.execute(text("SELECT count(*) FROM jurisdictions")).scalar_one() == 38
        assert conn.execute(text("SELECT count(*) FROM personas")).scalar_one() == 5

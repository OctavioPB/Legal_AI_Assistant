"""WS1.T01 — closed catalogs: jurisdictions (MX federal + 32 states + demo municipalities, US
federal + CA) and personas. These are the only source of jurisdiction/persona values: SessionContext
and the UI validate against them (jurisdictions.md), so there is no free text anywhere downstream.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# (state_code, name_es, name_en)
MX_STATES = [
    ("AGS", "Aguascalientes", "Aguascalientes"),
    ("BC", "Baja California", "Baja California"),
    ("BCS", "Baja California Sur", "Baja California Sur"),
    ("CAMP", "Campeche", "Campeche"),
    ("CHIS", "Chiapas", "Chiapas"),
    ("CHIH", "Chihuahua", "Chihuahua"),
    ("CDMX", "Ciudad de México", "Mexico City"),
    ("COAH", "Coahuila", "Coahuila"),
    ("COL", "Colima", "Colima"),
    ("DGO", "Durango", "Durango"),
    ("GTO", "Guanajuato", "Guanajuato"),
    ("GRO", "Guerrero", "Guerrero"),
    ("HGO", "Hidalgo", "Hidalgo"),
    ("JAL", "Jalisco", "Jalisco"),
    ("MEX", "Estado de México", "State of Mexico"),
    ("MICH", "Michoacán", "Michoacán"),
    ("MOR", "Morelos", "Morelos"),
    ("NAY", "Nayarit", "Nayarit"),
    ("NL", "Nuevo León", "Nuevo León"),
    ("OAX", "Oaxaca", "Oaxaca"),
    ("PUE", "Puebla", "Puebla"),
    ("QRO", "Querétaro", "Querétaro"),
    ("QROO", "Quintana Roo", "Quintana Roo"),
    ("SLP", "San Luis Potosí", "San Luis Potosí"),
    ("SIN", "Sinaloa", "Sinaloa"),
    ("SON", "Sonora", "Sonora"),
    ("TAB", "Tabasco", "Tabasco"),
    ("TAMPS", "Tamaulipas", "Tamaulipas"),
    ("TLAX", "Tlaxcala", "Tlaxcala"),
    ("VER", "Veracruz", "Veracruz"),
    ("YUC", "Yucatán", "Yucatán"),
    ("ZAC", "Zacatecas", "Zacatecas"),
]
assert len(MX_STATES) == 32

# Demo municipalities: two siblings in NL (isolation probes) and one in JAL.
MX_MUNICIPALITIES = [
    ("NL", "Monterrey"),
    ("NL", "San Pedro Garza García"),
    ("JAL", "Guadalajara"),
]

US_STATES = [("CA", "California", "California")]

# (code, label_es, label_en, legal_domain[])
PERSONAS = [
    ("general", "General", "General", []),
    ("labor", "Laboral", "Labor & employment", ["labor"]),
    ("criminal", "Penal", "Criminal", ["criminal"]),
    ("corporate", "Corporativo", "Corporate", ["corporate"]),
    ("corporate_finance", "Corporativo-financiero", "Corporate finance", ["corporate", "finance"]),
]


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    # Fail early with a useful message if the cluster was not bootstrapped (docker/initdb).
    op.execute(
        """
        DO $$
        BEGIN
          IF (SELECT count(*) FROM pg_roles WHERE rolname IN
              ('legalai_migrator','legalai_app','corpus_ingestor','legalai_provisioner')) <> 4 THEN
            RAISE EXCEPTION 'Missing roles: run docker/initdb/00-roles.sh (see docker-compose.yml)';
          END IF;
          IF (SELECT count(*) FROM pg_extension WHERE extname IN
              ('vector','ltree','unaccent','pgcrypto')) <> 4 THEN
            RAISE EXCEPTION 'Missing extensions: run docker/initdb/01-bootstrap-db.sql as superuser';
          END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE jurisdictions (
            id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            country       text NOT NULL CHECK (country IN ('MX','US')),
            level         text NOT NULL CHECK (level IN ('federal','state','municipal')),
            state_code    text CHECK (state_code ~ '^[A-Z]{2,5}$'),
            municipality  text CHECK (municipality <> '' AND municipality = btrim(municipality)),
            name_es       text NOT NULL,
            name_en       text NOT NULL,
            -- shape: federal has neither, state has a code, municipal has both
            CONSTRAINT jurisdictions_shape_ck CHECK (
                (level = 'federal'  AND state_code IS NULL     AND municipality IS NULL) OR
                (level = 'state'    AND state_code IS NOT NULL AND municipality IS NULL) OR
                (level = 'municipal' AND state_code IS NOT NULL AND municipality IS NOT NULL)),
            -- NULLS NOT DISTINCT: two 'federal' rows of one country would be duplicates (a plain
            -- UNIQUE treats NULLs as distinct and would allow them).
            CONSTRAINT jurisdictions_natural_key
                UNIQUE NULLS NOT DISTINCT (country, level, state_code, municipality)
        )
        """
    )

    # Stacking needs the parents to exist: a state needs its country's federal row, a municipality
    # needs its state. Enforced here so a catalog typo cannot create an orphan that stacks nowhere.
    op.execute(
        """
        CREATE FUNCTION jurisdictions_check_parent() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.level IN ('state','municipal') AND NOT EXISTS (
               SELECT 1 FROM jurisdictions j
               WHERE j.country = NEW.country AND j.level = 'federal') THEN
            RAISE EXCEPTION 'jurisdictions: no federal row for country %', NEW.country;
          END IF;
          IF NEW.level = 'municipal' AND NOT EXISTS (
               SELECT 1 FROM jurisdictions j
               WHERE j.country = NEW.country AND j.level = 'state'
                 AND j.state_code = NEW.state_code) THEN
            RAISE EXCEPTION 'jurisdictions: no state % in % for municipality %',
                            NEW.state_code, NEW.country, NEW.municipality;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER jurisdictions_parent_trg BEFORE INSERT OR UPDATE ON jurisdictions "
        "FOR EACH ROW EXECUTE FUNCTION jurisdictions_check_parent()"
    )

    op.execute(
        """
        CREATE TABLE personas (
            code          text PRIMARY KEY CHECK (code ~ '^[a-z_]+$'),
            label_es      text NOT NULL,
            label_en      text NOT NULL,
            legal_domain  text[] NOT NULL DEFAULT '{}'   -- soft-boost domains (rag.md)
        )
        """
    )

    rows: list[tuple[str, str, str | None, str | None, str, str]] = [
        ("MX", "federal", None, None, "Federal (México)", "Federal (Mexico)"),
        *[("MX", "state", c, None, es, en) for c, es, en in MX_STATES],
    ]
    names = {c: (es, en) for c, es, en in MX_STATES}
    rows += [
        ("MX", "municipal", st, mun, f"{mun}, {names[st][0]}", f"{mun}, {names[st][1]}")
        for st, mun in MX_MUNICIPALITIES
    ]
    rows.append(
        ("US", "federal", None, None, "Federal (Estados Unidos)", "Federal (United States)")
    )
    rows += [("US", "state", c, None, es, en) for c, es, en in US_STATES]

    # Parents first (the trigger checks them): federal rows sort before states before municipal.
    order = {"federal": 0, "state": 1, "municipal": 2}
    rows.sort(key=lambda r: (r[0] != "MX", order[r[1]]))
    for country, level, state, muni, es, en in rows:
        op.execute(
            "INSERT INTO jurisdictions (country, level, state_code, municipality, name_es, name_en) "
            f"VALUES ({_lit(country)}, {_lit(level)}, {_lit(state)}, {_lit(muni)}, "
            f"{_lit(es)}, {_lit(en)})"
        )

    for code, es, en, domains in PERSONAS:
        arr = "ARRAY[" + ",".join(_lit(d) for d in domains) + "]::text[]" if domains else "'{}'"
        op.execute(
            f"INSERT INTO personas (code, label_es, label_en, legal_domain) "
            f"VALUES ({_lit(code)}, {_lit(es)}, {_lit(en)}, {arr})"
        )

    # Catalogs are read-only for every runtime role; only migrations change them.
    op.execute("GRANT SELECT ON jurisdictions, personas TO legalai_app, corpus_ingestor")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: reforms version, nothing is deleted (invariant 3).")

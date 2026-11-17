"""Seeding and query helpers for the database suites. Seeding goes through the production roles
and policies (app role under a tenant context, ingestor for the public corpus), so seeding itself
exercises RLS."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text

from app.db.models import Chunk, DocNode, Document
from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_session, tenant_transaction
from tests.fakes import fake_embedding, vector_literal

THRESHOLDS = {"min": 80, "target": 450, "max": 800}


def make_tenant(provisioner_engine: Engine, slug: str) -> uuid.UUID:
    with provisioner_engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO tenants (slug, name) VALUES (:s, :n) RETURNING id"),
            {"s": slug, "n": slug.upper()},
        ).scalar_one()


def jurisdiction_id(
    conn: Connection,
    country: str,
    level: str,
    state_code: str | None = None,
    municipality: str | None = None,
) -> int:
    return conn.execute(
        text(
            "SELECT id FROM jurisdictions WHERE country = :c AND level = :l "
            "AND state_code IS NOT DISTINCT FROM :s AND municipality IS NOT DISTINCT FROM :m"
        ),
        {"c": country, "l": level, "s": state_code, "m": municipality},
    ).scalar_one()


@dataclass(frozen=True)
class Article:
    """One article = one node = one chunk (enough to exercise every table and policy)."""

    cite_id: str
    label: str
    text: str


def seed_document(
    engine: Engine,
    tenant_id: uuid.UUID,
    *,
    doc_type: str,
    jurisdiction: tuple[str, str, str | None, str | None],
    title: str,
    articles: list[Article],
    language: str = "es",
    source: str | None = None,
    is_current: bool = True,
) -> list[str]:
    """Insert a document with a flat tree under `tenant_id`. The public corpus is written through
    the dedicated ingestor engine, everything else through the app role."""
    public = tenant_id == PUBLIC_CORPUS_TENANT_ID
    with tenant_session(engine, tenant_id, allow_public=public) as s:
        jid = jurisdiction_id(s.connection(), *jurisdiction)
        src = source or f"s3://bucket/{tenant_id}/{title}.pdf"
        doc = Document(
            tenant_id=tenant_id,
            jurisdiction_id=jid,
            doc_type=doc_type,
            legal_domain=["labor"],
            title=title,
            language=language,
            source_uri=src,
            checksum_sha256=hashlib.sha256(src.encode()).hexdigest(),
        )
        s.add(doc)
        s.flush()
        for i, art in enumerate(articles):
            node = DocNode(
                document_id=doc.id,
                tenant_id=tenant_id,
                node_type="articulo",
                label=art.label,
                ordinal=i,
                path=f"doc.art{i + 1}",
                full_text=art.text,
            )
            s.add(node)
            s.flush()
            crumb = f"[{title} › {art.label}]"
            s.add(
                Chunk(
                    tenant_id=tenant_id,
                    document_id=doc.id,
                    node_id=node.id,
                    is_current=is_current,
                    cite_id=art.cite_id,
                    citation_string=f"{title}, {art.label}",
                    text=art.text,
                    breadcrumb=crumb,
                    embedded_text=f"{crumb}\n{art.text}",
                    char_start=0,
                    char_end=len(art.text),
                    token_count=max(1, len(art.text.split())),
                    embedding=fake_embedding(f"{crumb} {art.text}"),
                    embedding_model="fake-embed",
                    embedding_revision="r1",
                    chunker_version=1,
                    chunk_thresholds=THRESHOLDS,
                )
            )
        s.flush()
    return [a.cite_id for a in articles]


# Same text, same numbering, in two different firms: the overlap that makes leaks visible.
CLIENT_FILE_ARTICLES = [
    Article(
        "CF-EXP-0001-ART47",
        "Artículo 47",
        "El patrón podrá rescindir por faltas de asistencia del trabajador.",
    ),
    Article(
        "CF-EXP-0001-ART48",
        "Artículo 48",
        "El trabajador podrá solicitar reinstalación o indemnización.",
    ),
]


def seed_overlapping_tenants(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """Public LFT + NL/JAL civil codes, and identical client files (same cite_ids!) in A and B."""
    seed_document(
        ingestor_engine,
        PUBLIC_CORPUS_TENANT_ID,
        doc_type="ley",
        jurisdiction=("MX", "federal", None, None),
        title="Ley Federal del Trabajo",
        articles=[
            Article(
                "MX-FED-LFT-ART47-v2025.06",
                "Artículo 47",
                "Son causas de rescisión sin responsabilidad para el patrón las faltas de asistencia.",
            ),
        ],
    )
    seed_document(
        ingestor_engine,
        PUBLIC_CORPUS_TENANT_ID,
        doc_type="codigo",
        jurisdiction=("MX", "state", "NL", None),
        title="Código Civil de Nuevo León",
        articles=[
            Article(
                "MX-NL-CCNL-ART1148-v2023",
                "Artículo 1148",
                "La prescripción adquisitiva de buena fe se consuma en cinco años.",
            )
        ],
    )
    seed_document(
        ingestor_engine,
        PUBLIC_CORPUS_TENANT_ID,
        doc_type="codigo",
        jurisdiction=("MX", "state", "JAL", None),
        title="Código Civil de Jalisco",
        articles=[
            Article(
                "MX-JAL-CCJ-ART889-v2022",
                "Artículo 889",
                "La prescripción adquisitiva de buena fe se consuma en cinco años.",
            )
        ],
    )
    for tenant in (tenant_a, tenant_b):
        seed_document(
            app_engine,
            tenant,
            doc_type="client_file",
            jurisdiction=("MX", "federal", None, None),
            title="Expediente 0001",
            articles=CLIENT_FILE_ARTICLES,
        )
        seed_product_rows(app_engine, tenant)
        seed_lineage(
            app_engine, tenant, "Expediente 0001", "CF-EXP-0001-ART47", "CF-EXP-0001-ART48"
        )
    seed_lineage(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID, "Ley Federal del Trabajo",
        "MX-FED-LFT-ART47-v2025.06", "MX-FED-LFT-ART47-v2026.01",
    )  # fmt: skip


def seed_conversation(app_engine: Engine, tenant: uuid.UUID) -> uuid.UUID:
    """A user with one case and one message: client content in every tenant (ADR-016)."""
    return seed_product_rows(app_engine, tenant)[1]


# The user `seed_product_rows` created per tenant: the anchor the visibility checks need (ADR-018).
SEEDED_USERS: dict[uuid.UUID, uuid.UUID] = {}


def seed_product_rows(app_engine: Engine, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Client content in every product table: a case with a message, a matter with a member, a note
    and a saved source, a matter-linked conversation and a verification report (ADR-016/018/019).
    Returns (user_id, case conversation id)."""
    with tenant_transaction(app_engine, tenant) as conn:
        user = conn.execute(
            text(
                "INSERT INTO users (tenant_id, email, display_name) "
                "VALUES (:t, 'seed@firm.mx', 'Seed') RETURNING id"
            ),
            {"t": tenant},
        ).scalar_one()
    SEEDED_USERS[tenant] = user
    with tenant_transaction(app_engine, tenant, user_id=user) as conn:
        conv = conn.execute(
            text(
                "INSERT INTO conversations (tenant_id, user_id, kind, title, language, "
                "jurisdiction_id, persona, case_side, case_facts, case_claim) "
                "VALUES (:t, :u, 'case', 'Despido de la trabajadora X', 'es', "
                "(SELECT id FROM jurisdictions WHERE country = 'MX' AND level = 'federal'), "
                "'labor', 'actor', 'Hechos confidenciales del cliente', 'Reinstalación') "
                "RETURNING id"
            ),
            {"t": tenant, "u": user},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO messages (tenant_id, conversation_id, seq, role, action, content) "
                "VALUES (:t, :c, 1, 'user', 'attack', 'Argumento privilegiado')"
            ),
            {"t": tenant, "c": conv},
        )
        matter = uuid.uuid4()  # no RETURNING: the row becomes visible once its member row exists
        conn.execute(
            text(
                "INSERT INTO matters (id, tenant_id, created_by, title) "
                "VALUES (:id, :t, :u, 'Pérez vs. ACME')"
            ),
            {"id": matter, "t": tenant, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO matter_members (tenant_id, matter_id, user_id, role) "
                "VALUES (:t, :m, :u, 'owner')"
            ),
            {"t": tenant, "m": matter, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO matter_notes (tenant_id, matter_id, author_id, body) "
                "VALUES (:t, :m, :u, 'Nota privilegiada')"
            ),
            {"t": tenant, "m": matter, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO matter_sources (tenant_id, matter_id, cite_id, added_by) "
                "VALUES (:t, :m, 'CF-EXP-0001-ART47', :u)"
            ),
            {"t": tenant, "m": matter, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO conversations (tenant_id, user_id, matter_id, kind, title, language, "
                "jurisdiction_id, persona) VALUES (:t, :u, :m, 'chat', 'Consulta del asunto', 'es', "
                "(SELECT id FROM jurisdictions WHERE country = 'MX' AND level = 'federal'), "
                "'labor')"
            ),
            {"t": tenant, "u": user, "m": matter},
        )
        conn.execute(
            text(
                "INSERT INTO verification_reports (tenant_id, user_id, matter_id, title, "
                "source_kind, summary, items) VALUES (:t, :u, :m, 'Demanda', 'text', "
                "'{}'::jsonb, '[]'::jsonb)"
            ),
            {"t": tenant, "u": user, "m": matter},
        )
        conn.execute(  # ADR-025: an uploaded file of the matter (the row; its text is a document)
            text(
                "INSERT INTO client_files (tenant_id, uploaded_by, matter_id, title, kind, "
                "language, jurisdiction_id, file_token, source_format) VALUES (:t, :u, :m, "
                "'Contrato privilegiado', 'contrato', 'es', "
                "(SELECT id FROM jurisdictions WHERE country = 'MX' AND level = 'federal'), "
                "'DSEED0001', 'text')"
            ),
            {"t": tenant, "u": user, "m": matter},
        )
        conn.execute(  # ADR-024: the firm's model policy (tenant-private)
            text(
                "INSERT INTO tenant_model_policy (tenant_id, updated_by) VALUES (:t, :u) "
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"t": tenant, "u": user},
        )
    return user, uuid.UUID(str(conv))


def seed_lineage(
    engine: Engine, tenant: uuid.UUID, title: str, old_cite: str, new_cite: str
) -> None:
    """A reform record for the document titled `title` (ADR-017): change, lineage row, source check.
    The public corpus goes through the ingestor engine, a firm's client file through the app role."""
    public = tenant == PUBLIC_CORPUS_TENANT_ID
    with tenant_transaction(engine, tenant, allow_public=public) as conn:
        doc = conn.execute(
            text("SELECT id FROM documents WHERE tenant_id = :t AND title = :title"),
            {"t": tenant, "title": title},
        ).scalar_one()
        change = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO corpus_changes (id, tenant_id, official_id, old_document_id, "
                "new_document_id, source_uri, counts) VALUES (:id, :t, 'DOC', :d, :d, 'file://x', "
                "'{}'::jsonb)"
            ),
            {"id": change, "t": tenant, "d": doc},
        )
        conn.execute(
            text(
                "INSERT INTO cite_lineage (tenant_id, change_id, old_cite_id, new_cite_id, change, "
                "citation_string) VALUES (:t, :c, :o, :n, 'amended', 'art. 47')"
            ),
            {"t": tenant, "c": change, "o": old_cite, "n": new_cite},
        )
        conn.execute(
            text(
                "INSERT INTO source_checks (tenant_id, official_id, source, outcome) "
                "VALUES (:t, 'DOC', 'file://x', 'unchanged')"
            ),
            {"t": tenant},
        )
        # ADR-026: an index entry and the source an operator approved from it
        conn.execute(
            text(
                "INSERT INTO corpus_index_entries (tenant_id, index_id, entry_key, title, url, "
                "status, source_key, decided_by, decided_at) VALUES (:t, 'DIP_FED', 'LDOC', "
                "'LEY de Prueba', 'https://example.invalid/LDOC.pdf', 'approved', 'LDOC', 'x', now())"
            ),
            {"t": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO corpus_sources (tenant_id, key, index_id, entry_key, title, "
                "source_url, country, level, scope, code, language, doc_type, legal_domain, "
                "grammar, approved_by) VALUES (:t, 'LDOC', 'DIP_FED', 'LDOC', 'Ley de Prueba', "
                "'https://example.invalid/LDOC.pdf', 'MX', 'federal', 'FED', 'LDOC', 'es', 'ley', "
                "ARRAY['civil'], 'mx', 'x')"
            ),
            {"t": tenant},
        )


def search_like_retrieval(conn: Connection, query: str, k: int = 50) -> list[dict[str, object]]:
    """Both retrieval arms as plain SQL (the real pipeline arrives in WS3): semantic cosine top-k
    and lexical websearch top-k. Used to assert that retrieval results respect tenancy."""
    q = vector_literal(fake_embedding(query))
    semantic = (
        conn.execute(
            text(
                "SELECT id, tenant_id, cite_id, 'semantic' AS arm FROM chunks "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
            ),
            {"q": q, "k": k},
        )
        .mappings()
        .all()
    )
    lexical = (
        conn.execute(
            text(
                "SELECT id, tenant_id, cite_id, 'lexical' AS arm FROM chunks "
                "WHERE fts @@ websearch_to_tsquery('legal_es', :t) LIMIT :k"
            ),
            {"t": query, "k": k},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in (*semantic, *lexical)]


def visible_tenants(
    engine: Engine, tenant_id: uuid.UUID, table: str, user_id: uuid.UUID | None = None
) -> set[uuid.UUID]:
    with tenant_transaction(engine, tenant_id, user_id=user_id) as conn:
        # table names here are the test registry's constants, never user input
        rows = conn.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars().all()
    return set(rows)


def seed_bulk(admin_engine: Engine, tenant_id: uuid.UUID, n: int = 3000, states: int = 8) -> None:
    """`n` chunks for one tenant spread over `states` Mexican states, inserted as superuser (RLS
    bypassed on purpose: this is volume for query-plan tests, not an isolation test). All vectors
    are the same constant - enough for the planner and for building the HNSW index."""
    vec = "[" + ",".join(["0.03125"] * 1024) + "]"
    with admin_engine.begin() as conn:
        codes = (
            conn.execute(
                text(
                    "SELECT state_code FROM jurisdictions WHERE country='MX' AND level='state' "
                    "ORDER BY state_code LIMIT :n"
                ),
                {"n": states},
            )
            .scalars()
            .all()
        )
        for code in codes:
            jid = jurisdiction_id(conn, "MX", "state", code, None)
            conn.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, jurisdiction_id, doc_type, title, language, "
                    "source_uri, checksum_sha256) VALUES (gen_random_uuid(), :t, :j, 'client_file', :c, 'es', "
                    ":c, repeat('b', 64))"
                ),
                {"t": tenant_id, "j": jid, "c": f"bulk-{code}"},
            )
        conn.execute(
            text(
                """
                WITH docs AS (SELECT id, row_number() OVER (ORDER BY title) AS k FROM documents
                              WHERE tenant_id = :t AND title LIKE 'bulk-%'),
                nodes AS (
                  INSERT INTO doc_nodes (document_id, tenant_id, node_type, label, ordinal, path, full_text)
                  SELECT d.id, :t, 'articulo', 'a' || g, g, ('n' || g)::ltree, 'texto'
                  FROM generate_series(1, :n) g JOIN docs d ON d.k = 1 + (g % :s)
                  RETURNING id, document_id, ordinal)
                INSERT INTO chunks (tenant_id, document_id, node_id, jurisdiction_id, country, level,
                                    doc_type, language, cite_id, citation_string, text, breadcrumb,
                                    embedded_text, char_start, char_end, token_count, embedding,
                                    embedding_model, embedding_revision, chunker_version, chunk_thresholds)
                SELECT :t, n.document_id, n.id, 0, '', '', '', '', 'CF-BULK-' || n.ordinal, 'c',
                       'texto ' || n.ordinal, '[b]', '[b] texto', 0, 10, 5, CAST(:v AS vector),
                       'fake-embed', 'r1', 1, '{}'::jsonb
                FROM nodes n
                """
            ),
            {"t": tenant_id, "n": n, "s": len(codes), "v": vec},
        )
        conn.execute(text("ANALYZE chunks"))

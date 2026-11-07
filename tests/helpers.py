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


def visible_tenants(engine: Engine, tenant_id: uuid.UUID, table: str) -> set[uuid.UUID]:
    with tenant_transaction(engine, tenant_id) as conn:
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

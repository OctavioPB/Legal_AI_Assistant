"""ADR-037 (D): character-level precision/recall reads real chunk offsets under RLS."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.model import DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from evals.golden import char_overlap
from evals.run import _like_prefix, spans
from tests.fakes import fake_embedding
from tests.helpers import jurisdiction_id
from tests.ingestion.fixtures import FIXTURES


class Emb:
    model, revision = "fake-embed", "r1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [fake_embedding(t) for t in texts]


def _seed(ingestor_engine: Engine, admin_engine: Engine) -> list[str]:
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
    ingest_document(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID,
        meta=DocMeta(country="MX", scope="FED", code="LFT", title="Ley X", version_label="1"),
        doc_type="ley", jurisdiction_id=fed, source_uri="file://lft.txt",
        raw_text=FIXTURES["nested_inciso"], embedder=Emb(), counter=ApproxCounter(), official_id="LFT",
    )  # fmt: skip
    with admin_engine.connect() as c:
        return list(c.execute(text("SELECT cite_id FROM chunks ORDER BY char_start")).scalars())


def test_spans_read_exact_ids_and_stems_and_the_overlap_is_measured_in_characters(
    ingestor_engine: Engine, admin_engine: Engine, app_engine: Engine, tenant_a: uuid.UUID
) -> None:
    ids = _seed(ingestor_engine, admin_engine)
    assert len(ids) >= 2
    first, second = ids[0], ids[1]
    stem = first.rsplit("-v", 1)[0][: len("MX-FED-LFT-ART20")] + "*"  # every unit of art. 20
    with tenant_transaction(app_engine, tenant_a) as conn:
        hit_spans, gold_spans = spans(conn, [first], (second,))
        assert len(hit_spans) == 1 and len(gold_spans) == 1
        p, r = char_overlap(hit_spans, gold_spans)
        assert p == 0.0 and r == 0.0  # two different units do not overlap
        same = spans(conn, [first], (first,))
        assert char_overlap(*same) == (1.0, 1.0)
        _, all_gold = spans(conn, [first], (stem,))
        assert len(all_gold) == len(ids)  # the stem covers every unit of the article
        p, r = char_overlap([hit_spans[0]], all_gold)
        assert p == 1.0 and 0 < r < 1  # the hit is gold, but only part of it


def test_like_prefix_escapes_wildcards_in_cite_ids() -> None:
    assert _like_prefix("MX-FED-REG_ADMIN_MTY-ART1") == "MX-FED-REG\\_ADMIN\\_MTY-ART1%"
    assert _like_prefix("A%B") == "A\\%B%"

"""A small law in two (three) versions, ingested through the real path, for the navigator, the
verifier, the matters' saved sources and the reform watcher (ADR-017/018/019)."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import Engine

from app.db.session import PUBLIC_CORPUS_TENANT_ID
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.parser import ParsedDocument
from app.ingestion.store import IngestStats, ingest_document
from app.ingestion.tokens import ApproxCounter
from app.ingestion.watch import FetchResult
from app.settings import Settings
from tests.fakes import FakeEmbedder

FILL = " ".join(["considerando"] * 70)  # each fracción stays its own chunk

HEAD = """LEY FEDERAL DEL TRABAJO

Última reforma publicada DOF {date}

TITULO SEGUNDO

Relaciones Individuales de Trabajo

CAPITULO IV

Rescisión de las relaciones de trabajo

"""
ART47_CAPUT = (
    "Artículo 47.- Son causas de rescisión de la relación de trabajo, sin responsabilidad para "
    "el patrón:"
)
FRAC_I = f"I. Engañarlo el trabajador con certificados falsos respecto de su capacidad {FILL};"
FRAC_II_V1 = (
    f"II. Incurrir el trabajador en faltas de probidad u honradez durante sus labores {FILL};"
)
FRAC_II_V2 = f"II. Incurrir el trabajador en faltas de probidad, honradez o acoso durante sus labores {FILL};"
FRAC_III = f"III. Cometer el trabajador actos de hostigamiento en contra de sus compañeros {FILL};"
ART48 = (
    "Artículo 48.- El trabajador podrá solicitar ante el Tribunal que se le reinstale en el "
    f"trabajo que desempeñaba, o que se le indemnice con el importe de tres meses de salario {FILL}"
)
ART49 = (
    f"Artículo 49.- El patrón quedará eximido de la obligación de reinstalar al trabajador {FILL}"
)
ART50_V1 = f"Artículo 50.- Las indemnizaciones consistirán en veinte días de salario por año {FILL}"
ART50_V2 = "Artículo 50.- Se deroga."
ART50BIS = f"Artículo 50 Bis.- La indemnización se pagará en una sola exhibición {FILL}"


def v1() -> str:
    return HEAD.format(date="14-05-2026") + "\n\n".join(
        [ART47_CAPUT, FRAC_I, FRAC_II_V1, ART48, ART49, ART50_V1]
    )


def v2() -> str:
    """Art. 47-II amended, 47-III added, 48 unchanged, 49 removed, 50 derogated, 50 Bis added."""
    return HEAD.format(date="01-10-2026") + "\n\n".join(
        [ART47_CAPUT, FRAC_I, FRAC_II_V2, FRAC_III, ART48, ART50_V2, ART50BIS]
    )


def v3() -> str:
    """Only 47-I amended: 48 is unchanged again (a chain of two reforms)."""
    return (
        v2().replace("01-10-2026", "20-10-2026").replace("certificados falsos", "documentos falsos")
    )


# The chunk thresholds of ChunkThresholds() defaults, so the watcher (which builds them from the
# settings, like the seed) writes into the SAME generation as `ingest_law` (invariant 5).
WATCH_SETTINGS = Settings(refusal_tau=0.3, chunk_max_tokens=400, embedder_window=512)
EMB = FakeEmbedder()


def ingest_law(engine: Engine, raw: str, version: str = "2026.05.14") -> IngestStats:
    meta = DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
                   version_label=version, language="es")  # fmt: skip
    return ingest_document(
        engine, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type="ley",
        jurisdiction_id=_federal(engine), source_uri=f"file://LFT-{version}.txt", raw_text=raw,
        embedder=EMB, counter=ApproxCounter(),
        thresholds=ChunkThresholds.from_settings(WATCH_SETTINGS, ApproxCounter().name),
        official_id="LFT", legal_domain=["labor"],
    )  # fmt: skip


def _federal(engine: Engine) -> int:
    from app.db.session import tenant_transaction
    from tests.helpers import jurisdiction_id

    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        return jurisdiction_id(c, "MX", "federal")


def pdf(raw: str) -> bytes:
    return b"%PDF-1.7\n" + raw.encode("utf-8")


class FakeParser:
    """Reads the fake PDF written by `pdf()`; remembers the paths it was given."""

    name = "fake-pdf"

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def parse(self, path: Path) -> ParsedDocument:
        self.paths.append(path)
        body = path.read_bytes()
        return ParsedDocument(text=body.split(b"\n", 1)[1].decode("utf-8"), page_count=1,
                              parser=self.name)  # fmt: skip


class FakeFetcher:
    """url -> FetchResult (or a list served in order). Records every call; never touches a socket."""

    def __init__(
        self,
        routes: dict[str, FetchResult | list[FetchResult]],
        files: dict[str, bytes] | None = None,
        etags: dict[str, str] | None = None,
    ) -> None:
        self.routes = routes
        self.files = files or {}  # url -> bytes served with HEAD (size, ETag) and byte ranges
        self.etags = etags or {}
        self.range_calls = 0
        self.calls: list[tuple[str, str, str | None, str | None]] = []

    def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None,
        method: str = "GET", byte_range: tuple[int, int] | None = None,
    ) -> FetchResult:  # fmt: skip
        self.calls.append((method, url, etag, last_modified))
        if byte_range is not None and url in self.files:  # a file served by ranges (leginfo)
            self.range_calls += 1
            data = self.files[url]
            start, end = byte_range
            return FetchResult(206, data[start : end + 1])
        if method == "HEAD" and url in self.files:
            return FetchResult(200, b"", self.etags.get(url), None, size=len(self.files[url]))
        r = self.routes.get(url, FetchResult(404, error="HTTP 404"))
        if isinstance(r, list):
            return r.pop(0) if len(r) > 1 else r[0]
        return r


ROBOTS = "https://www.diputados.gob.mx/robots.txt"
LFT_URL = "https://www.diputados.gob.mx/LeyesBiblio/pdf/LFT.pdf"


def ok(body: bytes, etag: str = '"e1"') -> FetchResult:
    return FetchResult(200, body, etag, "Thu, 01 Oct 2026 00:00:00 GMT")


def new_id() -> uuid.UUID:
    return uuid.uuid4()

"""The end-to-end harness (`evals/e2e/`) is code and is tested like code: the case files hold
together, the runner walks the app in order and cleans up, and the review packet never truncates a
cited text nor lets model or statute text break the page."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from evals.e2e import review, run_case

CASE = Path(run_case.ROOT) / "laboral_01"
LONG_ARTICLE = "Artículo 47.- " + " ".join(f"palabra{i}" for i in range(900)) + " FIN-DEL-ARTICULO"


def guion() -> dict[str, Any]:
    return json.loads((CASE / "guion.json").read_text("utf-8"))


def test_the_case_files_hold_together() -> None:
    g = guion()
    assert g["authored_by"] == "ai-draft" and g["reviewed_by"] is None, (
        "a lawyer has not approved it"
    )
    ids = (
        [c["id"] for c in g["chat"]]
        + [g["research"]["id"]]
        + [r["id"] for r in g["case"]["rounds"]]
    )
    assert len(ids) == len(set(ids))
    for item in [*g["chat"], g["research"]]:
        assert item["question"].strip() and item["expected_points"] and item["expected_articles"]
    assert {r["action"] for r in g["case"]["rounds"]} == {"strengthen", "attack"}
    assert g["case"]["side"] in {"actor", "demandado", "defensa", "acusacion", "tercero"}
    for rel in (g["file"]["path"], g["verify"]["path"]):
        assert (CASE / rel).is_file()
    assert "FICTICIO" in (CASE / "contrato.txt").read_text("utf-8")
    assert "FICTICIO" in (CASE / "escrito.txt").read_text("utf-8")
    assert len(g["verify"]["seeded"]) >= 5


# ---- the runner, against a fake API -----------------------------------------------------------------
class FakeApi:
    """Answers the routes the runner uses; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.token = "t"

    def call(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
             ctype: str = "application/json", stream: bool = False) -> Any:  # fmt: skip
        self.calls.append((method, path))
        if path == "/me":
            return {"role": "admin"}
        if path == "/models":
            return {"options": []}
        if path == "/catalog/jurisdictions":
            return [{"id": 1, "country": "MX", "level": "federal"}]
        if path == "/matters" and method == "POST":
            return {"id": "m1"}
        if path.startswith("/files"):
            return {"id": "f1", "status": "ready", "chunk_count": 3}
        if path == "/conversations":
            return {"id": f"c{len(self.calls)}"}
        if path.endswith("/messages/stream"):
            answer = (
                "La falta de aviso presume el despido injustificado [cite: MX-FED-LFT-ART47-v1]."
            )
            return [("stage", "retrieving"), ("result", {"result": {
                "status": "answered", "answer": answer, "refusal_reason": None, "request_id": "r1",
                "citations": [{"cite_id": "MX-FED-LFT-ART47-v1", "citation_string": "LFT, art. 47"}]}})]  # fmt: skip
        if path.startswith("/sources/"):
            return {"text": "Unidad citada.", "article_text": LONG_ARTICLE, "article_label": "Artículo 47",
                    "document_title": "LFT", "version_label": "v1", "is_current": True, "is_public": True}  # fmt: skip
        if path.startswith("/traces/"):
            return {"attempts": [{"output": "x", "verdict": {"cited": ["MX-FED-LFT-ART47-v1"]},
                                  "resolved_model_id": "fake-1"}]}  # fmt: skip
        if path == "/verify":
            return {"summary": {"total": 1}, "findings": [{"ref": "art. 47, LFT", "status": "vigente",
                                                           "quote": "coincide"}]}  # fmt: skip
        if path.endswith("/cheatsheets/stream"):
            return [("result", {"status": "refused", "refusal_reason": "validation.unsupported",
                                "citations": []})]  # fmt: skip
        if method in ("POST", "DELETE"):
            return None
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.mark.parametrize("keep", [False, True])
def test_the_runner_walks_the_app_in_order_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep: bool
) -> None:
    monkeypatch.setattr(run_case, "RUNS", tmp_path)
    api = FakeApi()
    out = run_case.run("laboral_01", api, keep, io.StringIO())  # type: ignore[arg-type]
    rec = json.loads(out.read_text("utf-8"))
    g = guion()
    assert [s["id"] for s in rec["steps"]["chat"]] == [c["id"] for c in g["chat"]]
    assert rec["steps"]["research"]["status"] == "answered"
    assert [s["action"] for s in rec["steps"]["case"]] == [r["action"] for r in g["case"]["rounds"]]
    ev = rec["steps"]["chat"][0]["evidence"][0]
    assert ev["article_text"].endswith("FIN-DEL-ARTICULO"), "the full article is recorded"
    assert rec["steps"]["chat"][0]["trace"]["attempts"][0]["model"] == "fake-1"
    assert rec["steps"]["cheatsheet"]["status"] == "refused"
    order = [p for _, p in api.calls]
    assert order.index("/verify") < next(
        i for i, p in enumerate(order) if p.endswith("/cheatsheets/stream")
    )
    deleted = ("DELETE", "/matters/m1") in api.calls
    assert deleted is (not keep), "the matter is deleted unless --keep"
    assert rec["git"].keys() >= {"commit", "dirty"}, "the run records what code produced it"


# ---- the review packet ------------------------------------------------------------------------------
def fake_run() -> dict[str, Any]:
    g = guion()
    src = {"cite_id": "MX-FED-LFT-ART47-v1", "citation_string": "LFT, art. 47", "unit_text": "Unidad.",
           "article_text": LONG_ARTICLE, "article_label": "Artículo 47", "version_label": "v1"}  # fmt: skip
    hostile = "Respuesta [cite: MX-FED-LFT-ART47-v1]. </script><script>alert(1)</script>"
    step = {"status": "answered", "answer": hostile, "refusal_reason": None, "seconds": 3.0,
            "evidence": [src], "trace": {"attempts": [
                {"output": "a", "verdict": {"cited": [], "invalid": ["X-1"]}, "model": "m"},
                {"output": "b", "verdict": {"cited": ["MX-FED-LFT-ART47-v1"]}, "model": "m"}]}}  # fmt: skip
    chat = [{"id": c["id"], "question": c["question"], **step} for c in g["chat"]]
    chat[-1] = {**chat[-1], "status": "refused", "answer": None, "evidence": [], "trace": None,
                "refusal_reason": "retrieval.below_threshold"}  # fmt: skip
    return {
        "case_id": g["id"], "case_dir": "laboral_01", "started_at": "2026-11-17T22:51:29+00:00",
        "git": {"commit": "abc123", "dirty": True}, "models": {}, "guion": g,
        "steps": {
            "chat": chat,
            "research": {"id": "I1", "question": g["research"]["question"], **step},
            "case": [{**r, **step} for r in g["case"]["rounds"]],
            "verify": {"summary": {"total": 1}, "findings": [{"ref": "art. 47", "status": "vigente",
                                                              "quote": "coincide"}]},
            "cheatsheet": {"status": "refused", "refusal_reason": "validation.unsupported",
                           "citations": [], "evidence": [], "seconds": 9,
                           "validator": [{"cited": [], "invalid": ["MX-FED-LFT-ART436"]}] * 2},
        },
    }  # fmt: skip


def test_the_packet_never_truncates_a_cited_text_and_numbers_citations() -> None:
    r = review.build(fake_run())
    md = review.markdown(r)
    assert md.count("FIN-DEL-ARTICULO") >= 1, "the whole article reaches the reviewer"
    assert "Respuesta [1]." in md, "citations become the numbers of the sources listed below"
    assert "rechazó (retrieval.below_threshold)" in md
    assert "el 1.er intento falló: citas a fuentes no mostradas: X-1" in md
    sheet = next(i for i in r["items"] if i["kind"] == "sheet")
    assert (
        sheet["auto"]["attempts"] == 2 and "MX-FED-LFT-ART436" in sheet["auto"]["final_problems"][0]
    )
    assert "borrador IA" in md and "CASO FICTICIO" in md
    assert len(r["items"]) == len(guion()["chat"]) + 1 + len(guion()["case"]["rounds"]) + 1


def test_the_page_carries_the_data_and_text_cannot_close_its_script(tmp_path: Path) -> None:
    path = tmp_path / "E2E-LAB-01-TEST.json"
    path.write_text(json.dumps(fake_run(), ensure_ascii=False), "utf-8")
    assert review.main([str(path)]) == 0
    page = (tmp_path / "E2E-LAB-01-TEST-review.html").read_text("utf-8")
    assert "/*REVIEW_DATA*/null" not in page
    script = page.split("const DATA = ", 1)[1].split(";\nconst $ = ", 1)[0]
    assert "</script>" not in script, "statute or model text cannot end the script"
    data = json.loads(script)
    assert data["case_id"] == "E2E-LAB-01" and data["items"][0]["sources"][0]["n"] == 1
    assert re.search(r"<title>Revisión E2E-LAB-01</title>", page)
    assert (tmp_path / "E2E-LAB-01-TEST-revision.md").is_file()


def test_the_template_has_one_data_slot() -> None:
    page = review.PAGE.read_text("utf-8")
    assert page.count("/*REVIEW_DATA*/null") == 1
    assert "localStorage" in page and 'claude.use("db")' in page, (
        "saves online, keeps a local draft"
    )

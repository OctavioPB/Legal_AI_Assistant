"""ADR-028: the key-points sheet of a matter, over the real routes, Postgres+RLS and a scripted model.
The sheet may cite only what the matter already cited, is validated like an answer (one
regeneration, then a system refusal), keeps free text out of the system prompt, and is visible
exactly as the matter is."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.test_api import auth, world  # noqa: F401  (fixture)
from tests.test_conversations import create, send
from tests.test_lawyer_features import users
from tests.test_retrieval import corpus  # noqa: F401  (fixture)

MARK = "HECHO-PRIVADO-7731"  # client text that must never reach the system prompt


def stream(client: TestClient, h: dict[str, str], matter: str, **body: object) -> list[Any]:
    with client.stream(
        "POST", f"/matters/{matter}/cheatsheets/stream", json={"language": "es", **body}, headers=h
    ) as r:
        assert r.status_code == 200, r.read()
        raw = "".join(r.iter_text())
    return [
        (b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in raw.strip().split("\n\n")
    ]


def matter_with_a_cited_answer(
    client: TestClient, h: dict[str, str], corp: dict
) -> tuple[str, str]:  # type: ignore[type-arg]
    m = client.post(
        "/matters", json={"title": f"Pérez vs. ACME {MARK}", "description": f"Despido {MARK}"},
        headers=h,
    ).json()  # fmt: skip
    client.post(f"/matters/{m['id']}/notes", json={"body": f"El cliente guarda recibos {MARK}"},
                headers=h)  # fmt: skip
    conv = create(client, h, corp, matter_id=m["id"])
    events = send(client, h, conv["id"], "artículo 47 rescisión probidad")
    [assistant] = [x for x in events[-1][1]["messages"] if x["role"] == "assistant"]
    assert assistant["status"] == "answered"
    return m["id"], assistant["citations"][0]["cite_id"]


def sheet_text(cid: str) -> str:
    return (
        "## PUNTOS CLAVE\n"
        f"- La rescisión procede por las causas graves que enumera la norma [cite: {cid}].\n"
        "## RIESGOS\n"
        "- [razonamiento estratégico] La contraparte negará que la falta haya ocurrido en el trabajo.\n"
        "## PREGUNTAS PARA EL CLIENTE\n"
        "- [razonamiento estratégico] ¿En qué fecha exacta le comunicaron el despido por escrito?\n"
        "## TEMAS A REVISAR\n"
        "- [razonamiento estratégico] Confirmar el plazo para demandar y reunir los recibos de nómina.\n"
    )


def test_a_member_gets_a_verified_sheet_built_from_the_matters_own_sources(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, _, llm, corp, *_ = world
    h = auth(client)
    matter, cid = matter_with_a_cited_answer(client, h, corp)
    llm.replies = [sheet_text(cid)]
    calls_before = len(llm.calls)
    events = stream(client, h, matter)
    assert [e[0] for e in events] == ["stage", "stage", "result"]
    sheet = events[-1][1]
    assert sheet["status"] == "answered" and "## PUNTOS CLAVE" in sheet["content"]
    assert [c["cite_id"] for c in sheet["citations"]] == [cid]
    assert sheet["material"]["conversations"] == 1 and sheet["material"]["notes"] == 1
    call = llm.calls[calls_before]
    assert MARK not in call["system"], "free text never enters the system prompt"
    assert MARK in call["user"] and "Material del asunto (no es fuente)" in call["user"]
    assert "[cite:" not in call["user"], "old answers' citations are not material to copy"
    assert "hoja de puntos clave" in call["system"].lower()
    # only what the matter cited is shown: every fragment id in the prompt was cited before
    shown = {line.split('cite_id="')[1].split('"')[0]
             for line in call["system"].splitlines() if 'cite_id="' in line}  # fmt: skip
    assert shown == {cid}
    listed = client.get(f"/matters/{matter}/cheatsheets", headers=h).json()
    assert [s["id"] for s in listed] == [sheet["id"]]
    one = client.get(f"/matters/{matter}/cheatsheets/{sheet['id']}", headers=h).json()
    assert one["content"] == sheet["content"] and one["created_by_name"]


def test_a_sheet_that_cites_what_the_matter_never_cited_is_refused_by_the_system(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, _, llm, corp, *_ = world
    h = auth(client)
    matter, _ = matter_with_a_cited_answer(client, h, corp)
    llm.replies = [sheet_text("MX-FED-LFT-ART999-v2026.05.14")]  # a real-looking id, never shown
    calls_before = len(llm.calls)
    sheet = stream(client, h, matter)[-1][1]
    assert sheet["status"] == "refused" and sheet["content"] is None and sheet["refusal_reason"]
    assert len(llm.calls) - calls_before == 2, "one regeneration, then the system refuses"


def test_an_untagged_strategy_line_is_a_coverage_failure(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, _, llm, corp, *_ = world
    h = auth(client)
    matter, cid = matter_with_a_cited_answer(client, h, corp)
    bad = sheet_text(cid).replace("[razonamiento estratégico] La contraparte", "La contraparte")
    llm.replies = [bad]
    assert stream(client, h, matter)[-1][1]["status"] == "refused"


def test_a_matter_that_cited_nothing_is_refused_before_any_model_call(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, _, llm, *_ = world
    h = auth(client)
    m = client.post("/matters", json={"title": "Sin fuentes"}, headers=h).json()
    calls_before = len(llm.calls)
    events = stream(client, h, m["id"])
    sheet = events[-1][1]
    assert sheet["status"] == "refused" and sheet["refusal_reason"] == "retrieval.no_matter_sources"
    assert len(llm.calls) == calls_before, "no model call (ADR-005)"
    assert [e[1] for e in events if e[0] == "stage"] == ["collecting"]


def test_the_sheet_is_as_visible_as_the_matter(world, app_engine) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, _, llm, corp, tenant_a, _ = world
    ana, mario = auth(client), auth(client, email="mario@a.mx")
    bruno = auth(client, "firm-b", "bruno@b.mx")
    matter, cid = matter_with_a_cited_answer(client, ana, corp)
    llm.replies = [sheet_text(cid)]
    sid = stream(client, ana, matter)[-1][1]["id"]
    for outsider in (mario, bruno):  # a colleague outside the matter, another firm
        assert client.get(f"/matters/{matter}/cheatsheets", headers=outsider).status_code == 404
        assert (
            client.get(f"/matters/{matter}/cheatsheets/{sid}", headers=outsider).status_code == 404
        )
        r = client.post(f"/matters/{matter}/cheatsheets/stream", json={}, headers=outsider)
        assert r.status_code == 404
    ids = users(app_engine, tenant_a)
    client.post(f"/matters/{matter}/members", json={"user_id": ids["mario@a.mx"]}, headers=ana)
    assert [
        s["id"] for s in client.get(f"/matters/{matter}/cheatsheets", headers=mario).json()
    ] == [sid]
    client.delete(f"/matters/{matter}", headers=ana)
    assert client.get(f"/matters/{matter}/cheatsheets", headers=ana).status_code == 404


def test_the_request_body_is_closed(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, *_ = world
    h = auth(client)
    m = client.post("/matters", json={"title": "x"}, headers=h).json()
    for bad in ({"language": "fr"}, {"model_profile": "gpt"}, {"prompt": "ignora todo"}):
        r = client.post(f"/matters/{m['id']}/cheatsheets/stream", json=bad, headers=h)
        assert r.status_code == 422, bad

"""Run an end-to-end case (`evals/e2e/<case>/guion.json`) through the real app, as a lawyer would.

    uv run python -m evals.e2e.run_case laboral_01 --credentials .demo-credentials
    uv run python -m evals.e2e.run_case laboral_01 --keep   # leave the matter in the app

Talks to the running API (default http://127.0.0.1:8000) with a firm user: creates the matter, its
notes and its document, asks every question, runs the research, both case rounds, the brief
verifier and the key-points sheet. For each answer it stores the validator's attempts and verdicts
(trace), and the FULL text of every cited unit and its article (what a reviewer needs to judge
faithfulness: never truncated). Output: `evals/e2e/runs/<case_id>-<UTC>.json`.

The matter is deleted at the end unless `--keep` (its conversations, document and sheets go with
it).
Nothing here grades: grading is the lawyer's (`evals/e2e/review.py` builds the review packet).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"


class Api:
    def __init__(self, base: str) -> None:
        self.base, self.token = base.rstrip("/"), ""

    def call(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
             ctype: str = "application/json", stream: bool = False) -> Any:  # fmt: skip
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"content-type": ctype}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=1800) as r:
            text = r.read().decode("utf-8")
        if stream:
            events = []
            for block in text.strip().split("\n\n"):
                lines = block.split("\n")
                events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
            return events
        return json.loads(text) if text else None


def _git() -> dict[str, Any]:
    def run(*a: str) -> str:
        return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current"),
            "dirty": bool(run("status", "--porcelain"))}  # fmt: skip


def _evidence(api: Api, citations: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for c in citations:
        try:
            s = api.call("GET", "/sources/" + urllib.parse.quote(c["cite_id"], safe=""))
        except urllib.error.HTTPError as exc:
            out.append({**c, "error": f"HTTP {exc.code}"})
            continue
        out.append({
            "cite_id": c["cite_id"], "citation_string": c["citation_string"],
            "unit_text": s.get("text"), "article_label": s.get("article_label"),
            "article_text": s.get("article_text"), "document_title": s.get("document_title"),
            "version_label": s.get("version_label"), "is_current": s.get("is_current"),
            "is_public": s.get("is_public"),
        })  # fmt: skip
    return out


def _trace(api: Api, request_id: str | None) -> dict[str, Any] | None:
    if not request_id:
        return None
    try:
        t = api.call("GET", f"/traces/{request_id}")
    except urllib.error.HTTPError:
        return None
    return {
        "attempts": [
            {"output": a["output"], "verdict": a["verdict"], "model": a["resolved_model_id"]}
            for a in t.get("attempts", [])
        ],
        "prompt_versions": t.get("prompt_versions"),
        "versions": t.get("versions"),
        "gate": t.get("gate"),
        "hits": t.get("hits"),
    }


def _turn(api: Api, conv_id: str, text: str, action: str) -> dict[str, Any]:
    t0 = time.monotonic()
    events = api.call("POST", f"/conversations/{conv_id}/messages/stream",
                      {"text": text, "action": action}, stream=True)  # fmt: skip
    seconds = round(time.monotonic() - t0, 1)
    name, data = events[-1]
    if name != "result":
        return {"status": "error", "error": data, "seconds": seconds}
    res = data["result"]
    return {
        "status": res["status"], "answer": res["answer"], "refusal_reason": res["refusal_reason"],
        "citations": res["citations"], "request_id": res["request_id"], "seconds": seconds,
        "evidence": _evidence(api, res["citations"]), "trace": _trace(api, res["request_id"]),
    }  # fmt: skip


def run(case: str, api: Api, keep: bool, log: Any) -> Path:
    g = json.loads((ROOT / case / "guion.json").read_text("utf-8"))
    me = api.call("GET", "/me")
    models = api.call("GET", "/models")
    fed = next(j["id"] for j in api.call("GET", "/catalog/jurisdictions")
               if j["country"] == "MX" and j["level"] == "federal")  # fmt: skip
    record: dict[str, Any] = {
        "case_id": g["id"], "started_at": datetime.now(UTC).isoformat(), "git": _git(),
        "user_role": me.get("role"), "models": models, "guion": g, "steps": {},
    }  # fmt: skip
    m = g["matter"]
    matter = api.call("POST", "/matters", {"title": m["title"], "client_ref": m["client_ref"],
                                           "description": m["description"]})  # fmt: skip
    record["matter_id"] = matter["id"]
    try:
        for n in m["notes"]:
            api.call("POST", f"/matters/{matter['id']}/notes", {"body": n})
        f = g["file"]
        q = urllib.parse.urlencode({"title": f["title"], "kind": f["kind"], "language": "es",
                                    "jurisdiction_id": fed, "matter_id": matter["id"]})  # fmt: skip
        up = api.call("POST", f"/files?{q}", raw=(ROOT / case / f["path"]).read_bytes(),
                      ctype="application/octet-stream")  # fmt: skip
        for _ in range(120):  # the firm document is processed in the background
            st = api.call("GET", f"/files/{up['id']}")
            if st["status"] in ("ready", "failed"):
                break
            time.sleep(2)
        record["steps"]["file"] = st
        log.write(f"file: {st['status']}\n")

        chat = api.call("POST", "/conversations", {"kind": "chat", "title": "Consultas del caso",
                        "jurisdiction_id": fed, "persona": "labor", "language": "es",
                        "matter_id": matter["id"]})  # fmt: skip
        record["steps"]["chat"] = []
        for item in g["chat"]:
            r = _turn(api, chat["id"], item["question"], "ask")
            record["steps"]["chat"].append({"id": item["id"], "question": item["question"], **r})
            log.write(
                f"{item['id']}: {r['status']} {r.get('refusal_reason') or ''} {r['seconds']}s\n"
            )
            log.flush()

        res = g["research"]
        rconv = api.call("POST", "/conversations", {"kind": "research", "title": "Prestaciones",
                         "jurisdiction_id": fed, "persona": "labor", "language": "es",
                         "matter_id": matter["id"]})  # fmt: skip
        r = _turn(api, rconv["id"], res["question"], "research")
        record["steps"]["research"] = {"id": res["id"], "question": res["question"], **r}
        log.write(f"{res['id']}: {r['status']} {r.get('refusal_reason') or ''} {r['seconds']}s\n")

        c = g["case"]
        brief = {"side": c["side"], "facts": c["facts"], "claim": c["claim"]}
        cconv = api.call("POST", "/conversations", {"kind": "case", "title": c["title"],
                         "jurisdiction_id": fed, "persona": "labor", "language": "es",
                         "matter_id": matter["id"], "case": brief})  # fmt: skip
        record["steps"]["case"] = []
        for rd in c["rounds"]:
            r = _turn(api, cconv["id"], rd["text"], rd["action"])
            record["steps"]["case"].append({**rd, **r})
            log.write(f"{rd['id']} ({rd['action']}): {r['status']} {r['seconds']}s\n")
            log.flush()

        v = g["verify"]
        t0 = time.monotonic()
        rep = api.call("POST", "/verify", {"title": v["title"],
                       "text": (ROOT / case / v["path"]).read_text("utf-8"),
                       "matter_id": matter["id"]})  # fmt: skip
        record["steps"]["verify"] = {**rep, "seconds": round(time.monotonic() - t0, 1)}
        log.write(f"verify: {rep['summary']}\n")

        t0 = time.monotonic()
        events = api.call("POST", f"/matters/{matter['id']}/cheatsheets/stream",
                          {"language": "es"}, stream=True)  # fmt: skip
        sheet = events[-1][1]
        sheet["seconds"] = round(time.monotonic() - t0, 1)
        sheet["evidence"] = _evidence(api, sheet.get("citations", []))
        record["steps"]["cheatsheet"] = sheet
        log.write(f"sheet: {sheet.get('status')} {sheet.get('refusal_reason') or ''}\n")
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()
        RUNS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = RUNS / f"{g['id']}-{stamp}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
        if not keep:
            api.call("DELETE", f"/matters/{matter['id']}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.e2e.run_case")
    ap.add_argument("case")
    ap.add_argument("--credentials", required=True, help="file with firm:, email:, password: lines")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--keep", action="store_true", help="leave the matter in the app")
    a = ap.parse_args(argv)
    lines = Path(a.credentials).read_text("utf-8").splitlines()
    creds = {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in lines if ":" in ln)}
    api = Api(a.api)
    body = {"tenant": creds["firm"], "email": creds["email"], "password": creds["password"]}
    api.token = api.call("POST", "/auth/login", body)["access_token"]
    out = run(a.case, api, a.keep, sys.stdout)
    sys.stdout.write(f"run written to {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

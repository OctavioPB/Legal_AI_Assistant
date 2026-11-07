"""Model pins: generated from the runtime, checked for drift, validated against the real context."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import yaml

from app.orchestration.aliases import AliasRegistry, AliasSpec, default_aliases
from app.orchestration.pins import PLAN, drift, load_registry, validate_runtime
from app.settings import Settings


def spec(alias: str, model: str, digest: str, ctx: int, answer: int) -> dict[str, object]:
    return AliasSpec(
        alias=alias, model_id=f"{model}@{digest}", verified_on=date(2026, 11, 7),  # type: ignore[arg-type]
        source_url="http://localhost:11434/api/show", context_window=ctx, max_answer_tokens=answer,
    ).model_dump(mode="json")  # fmt: skip


def write_pins(
    path: Path, ctx: int = 262_144, answer: int = 4096, digest: str = "aaaaaaaaaaaa"
) -> None:
    data = {
        "aliases": {a: spec(a, m, digest, ctx, min(answer, t)) for a, (m, t, _) in PLAN.items()}
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_a_missing_pin_file_gives_an_unpinned_registry_that_refuses_to_serve(
    tmp_path: Path,
) -> None:
    reg = load_registry(tmp_path / "nope.yaml")
    assert all(v is None for v in reg.pinned_ids().values())
    assert validate_runtime(reg, Settings())[0].endswith("not pinned")


def test_a_pin_file_loads_into_a_pinned_registry(tmp_path: Path) -> None:
    write_pins(tmp_path / "p.yaml")
    reg = load_registry(tmp_path / "p.yaml")
    assert reg.spec("drafting-primary").model_id.endswith("@aaaaaaaaaaaa")  # type: ignore[union-attr]
    assert validate_runtime(reg, Settings()) == []


def test_runtime_validation_catches_a_context_that_cannot_hold_the_prompt(tmp_path: Path) -> None:
    write_pins(tmp_path / "p.yaml")
    reg = load_registry(tmp_path / "p.yaml")
    small = Settings(llm_num_ctx=8_192)  # 12k retrieved context cannot fit
    problems = validate_runtime(reg, small)
    assert len(problems) == len(PLAN) and all("llm_num_ctx" in p for p in problems)


def test_runtime_validation_catches_a_model_window_smaller_than_the_budget(tmp_path: Path) -> None:
    write_pins(tmp_path / "p.yaml", ctx=8_000)
    problems = validate_runtime(load_registry(tmp_path / "p.yaml"), Settings())
    assert any("exceeds the 8000-token window" in p for p in problems)


def tags(digests: dict[str, str]) -> httpx.MockTransport:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"models": [{"name": n, "digest": d} for n, d in digests.items()]}
        )

    return httpx.MockTransport(handler)


def test_drift_detects_a_replaced_or_removed_model(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    write_pins(tmp_path / "p.yaml", digest="aaaaaaaaaaaa")
    reg = load_registry(tmp_path / "p.yaml")
    import app.orchestration.pins as pins

    real = pins.model_digest
    models = {m for m, *_ in PLAN.values()}

    def fake(name: str, *, base_url: str) -> str:
        return real(
            name, base_url=base_url, transport=tags({m: "aaaaaaaaaaaa9999" for m in models})
        )

    monkeypatch.setattr(pins, "model_digest", fake)
    assert drift(reg, base_url="http://x") == []  # installed == pinned

    def changed(name: str, *, base_url: str) -> str:
        return real(
            name, base_url=base_url, transport=tags({m: "bbbbbbbbbbbb0000" for m in models})
        )

    monkeypatch.setattr(pins, "model_digest", changed)
    out = drift(reg, base_url="http://x")
    assert len(out) == len(PLAN) and all("a model change" in o for o in out)

    def removed(name: str, *, base_url: str) -> str:
        return real(name, base_url=base_url, transport=tags({}))

    monkeypatch.setattr(pins, "model_digest", removed)
    assert all("no longer installed" in o for o in drift(reg, base_url="http://x"))


def test_the_committed_pin_file_matches_the_plan() -> None:
    reg = load_registry()  # config/models.local.yaml, generated from the real runtime
    pinned = {a for a, s in ((a, reg.spec(a)) for a in PLAN) if s.pinned}
    assert pinned == set(PLAN), "run: uv run python -m app.orchestration.pins"
    assert AliasRegistry({**default_aliases(), **{a: reg.spec(a) for a in PLAN}})

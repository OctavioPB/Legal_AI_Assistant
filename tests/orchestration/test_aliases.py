from __future__ import annotations

from datetime import date

import pytest

from app.orchestration.aliases import (
    ALL_ALIASES,
    AliasRegistry,
    AliasSpec,
    UnpinnedAliasError,
    default_aliases,
)
from tests.fakes import FakeLLM

PIN = {
    "model_id": "provider-model-2026-01-01",
    "verified_on": date(2026, 11, 7),
    "source_url": "https://provider.example/docs/models",
    "context_window": 200_000,
}


def test_every_alias_exists_but_none_is_pinned_by_default() -> None:
    reg = AliasRegistry()
    assert set(default_aliases()) == set(ALL_ALIASES)
    assert all(v is None for v in reg.pinned_ids().values())
    for alias in ALL_ALIASES:
        with pytest.raises(UnpinnedAliasError):
            reg.require_pinned(alias)


def test_a_pin_is_all_or_nothing() -> None:
    with pytest.raises(ValueError, match="pinned together"):
        AliasSpec(alias="cheap-utility", max_answer_tokens=512, model_id="m")
    AliasSpec(alias="cheap-utility", max_answer_tokens=512, **PIN)  # type: ignore[arg-type]


def test_context_plus_answer_must_fit_the_window() -> None:
    specs = default_aliases()
    specs["drafting-primary"] = AliasSpec(
        alias="drafting-primary",
        max_answer_tokens=4096,
        fallbacks=("fallback-general",),
        **{**PIN, "context_window": 16_000},  # type: ignore[arg-type]
    )
    reg = AliasRegistry(specs)
    reg.validate_budget("drafting-primary", 11_000)
    with pytest.raises(ValueError, match="exceeds"):
        reg.validate_budget("drafting-primary", 12_000)  # the default 12k context budget + 4096


def test_fallback_cycles_and_unknown_fallbacks_are_refused() -> None:
    specs = default_aliases()
    specs["fallback-general"] = AliasSpec(
        alias="fallback-general", max_answer_tokens=1024, fallbacks=("drafting-primary",)
    )
    with pytest.raises(ValueError, match="cycle"):
        AliasRegistry(specs)
    specs = default_aliases()
    specs["cheap-utility"] = AliasSpec(
        alias="cheap-utility", max_answer_tokens=1, fallbacks=("cheap-utility",)
    )
    with pytest.raises(ValueError, match="invalid fallback"):
        AliasRegistry(specs)


def test_fake_llm_records_calls_and_scripts_replies() -> None:
    llm = FakeLLM(["primera", "segunda"])
    a = llm.complete("cheap-utility", system="S", user="U", max_tokens=10, tenant_id="t")
    b = llm.complete("cheap-utility", system="S", user="U", max_tokens=10, tenant_id="t")
    assert (a.text, b.text) == ("primera", "segunda") and len(llm.calls) == 2
    assert a.resolved_model_id == "fake-model-1"

"""Model profiles (ADR-024): which model family answers a conversation.

A closed catalog. The profile is chosen when a conversation is created, stored with it and never
changed (like jurisdiction and persona); every alias of the workflow resolves inside that profile's
own registry, so a conversation never silently switches model family.

- `gemma`  local (ADR-014): the pinned stack of `config/models.local.yaml` (default).
- `qwen`   local: every generation alias served by the small Qwen model of the same pin file.
- `claude` REMOTE: `config/models.anthropic.yaml`. Available only when the server has an API key;
           usable by a firm only after its admin opts in (`tenant_model_policy`). Content leaves
           the machine; the UI says so before the conversation is created.

Measurements (`config/model_measurements.yaml`) label each profile "measured" with the golden run
that backs it, or "not measured". Nothing becomes the default without a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from app.orchestration.aliases import ALL_ALIASES, AliasName, AliasRegistry, AliasSpec
from app.orchestration.client import LLMClient

ProfileKey = Literal["gemma", "qwen", "claude"]
PROFILE_KEYS: tuple[ProfileKey, ...] = get_args(ProfileKey)
Locality = Literal["local", "remote"]
LOCALITY: dict[ProfileKey, Locality] = {"gemma": "local", "qwen": "local", "claude": "remote"}

CONFIG = Path(__file__).resolve().parents[2] / "config"
ANTHROPIC_PINS = CONFIG / "models.anthropic.yaml"
MEASUREMENTS = CONFIG / "model_measurements.yaml"


class UnknownProfileError(ValueError):
    """Not in the closed catalog."""


class ProfileUnavailableError(RuntimeError):
    """The profile exists but this server cannot serve it (no key, SDK missing, not pinned)."""


def qwen_registry(local: AliasRegistry) -> AliasRegistry:
    """Every alias on the small local model, with the answer caps of the local plan and NO
    fallbacks (a fallback would only retry the same model)."""
    base = local.spec("fallback-general")
    specs: dict[AliasName, AliasSpec] = {
        a: base.model_copy(
            update={
                "alias": a,
                "max_answer_tokens": local.spec(a).max_answer_tokens,
                "fallbacks": (),
            }
        )
        for a in ALL_ALIASES
    }
    return AliasRegistry(specs)


def load_pins(path: Path) -> AliasRegistry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))["aliases"]
    return AliasRegistry({a: AliasSpec.model_validate(s) for a, s in data.items()})


def load_measurements(path: Path = MEASUREMENTS) -> dict[str, dict[str, Any] | None]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("profiles") or {})


@dataclass(frozen=True)
class ModelProfile:
    key: ProfileKey
    registry: AliasRegistry
    llm: LLMClient | None  # None: not served here (see `unavailable`)
    unavailable: str | None = None  # a catalog key the UI translates

    @property
    def locality(self) -> Locality:
        return LOCALITY[self.key]

    @property
    def model_id(self) -> str | None:
        return self.registry.spec("drafting-primary").model_id


@dataclass(frozen=True)
class ProfileCatalog:
    profiles: dict[ProfileKey, ModelProfile]
    default: ProfileKey = "gemma"
    measurements: dict[str, dict[str, Any] | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default not in self.profiles:
            raise ValueError(f"default profile {self.default!r} is not in the catalog")
        if LOCALITY[self.default] != "local":
            raise ValueError("the default profile must be local (ADR-014/024)")

    def get(self, key: str) -> ModelProfile:
        if key not in PROFILE_KEYS or key not in self.profiles:
            raise UnknownProfileError(key)
        return self.profiles[key]

    def serving(self, key: str) -> ModelProfile:
        """The profile, or ProfileUnavailableError when this server cannot serve it."""
        p = self.get(key)
        if p.llm is None:
            raise ProfileUnavailableError(p.unavailable or "unavailable")
        return p

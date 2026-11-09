"""Prompt composer (WS4.T02): five layers, versions pinned in `manifest.yaml`.

core -> workflow -> persona -> session -> fragments (rules/prompts.md).

Safety properties, all executable:
- templates are read-only inputs: the manifest pins every file's sha256, and loading a file whose
  bytes differ from its pin FAILS (a published version is never edited; a change is a new version);
- variables come only from closed enums / catalog labels (`SessionPrompt`); free user text never
  enters the system prompt;
- retrieved fragments are inserted as a VALUE after the templates are rendered, so braces, `%`,
  `<` or `{{ }}` inside statute text are inert;
- the same inputs give the same bytes (`ComposedPrompt.sha256`).

    uv run python -m app.prompts.composer pin     # add pins for versions not in the manifest yet
    uv run python -m app.prompts.composer verify  # check every pin
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, field_validator

PROMPTS_DIR = Path(__file__).parent
MANIFEST_PATH = PROMPTS_DIR / "manifest.yaml"

Workflow = Literal["dossier_builder", "adversarial", "conversar", "argumentar"]
Persona = Literal["general", "labor", "criminal", "corporate", "corporate_finance"]
Language = Literal["es-MX", "en-US"]
LANGUAGE_DISPLAY: dict[str, str] = {"es-MX": "español (México)", "en-US": "inglés (Estados Unidos)"}

# Example ids that appear inside prompts. The citation validator (WS5.T01) denylists them: a model
# that copies one is wrong by construction.
EXAMPLE_CITE_IDS = frozenset({"EJEMPLO-NO-CITAR"})
OWN_ANALYSIS_TAG = "[análisis propio]"
STRATEGIC_TAG = "[razonamiento estratégico]"

_WRAPPER = re.compile(r"<(/?)(contexto_recuperado)", re.IGNORECASE)
_LABEL = re.compile(r"^[\w .,·+()'\u2019/-]{1,200}$")  # no newline, braces, angle brackets, quotes


class ManifestError(RuntimeError):
    """The manifest and the template files disagree (missing pin, missing file, edited version)."""


class SessionPrompt(BaseModel):
    """Everything the system prompt may know about the session, all closed-catalog values."""

    model_config = ConfigDict(frozen=True)

    workflow: Workflow
    persona: Persona
    language: Language
    jurisdiction_display: str  # built from catalog names, e.g. "México · Federal + Nuevo León"

    @field_validator("jurisdiction_display")
    @classmethod
    def _catalog_label(cls, v: str) -> str:
        if not _LABEL.match(v):
            raise ValueError("jurisdiction_display must be a plain catalog label")
        return v


class ComposedPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str
    versions: tuple[str, ...]  # e.g. ("core/anti_hallucination@v1", ...) stored with every trace
    sha256: str


def _sha(data: bytes) -> str:
    """Line-ending independent: a CRLF checkout must pin to the same hash as an LF one."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _template_path(layer: str, name: str, version: str) -> Path:
    return PROMPTS_DIR / layer / f"{name}@{version}.j2"


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict[str, dict[str, str]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("layers", {})  # type: ignore[no-any-return]


def _entries(manifest: dict[str, dict[str, dict[str, str]]]) -> list[tuple[str, str, str, str]]:
    return [
        (layer, name, e["version"], e.get("sha256", ""))
        for layer, items in manifest.items()
        for name, e in items.items()
    ]


def verify_manifest(path: Path = MANIFEST_PATH) -> None:
    for layer, name, version, pin in _entries(load_manifest(path)):
        f = _template_path(layer, name, version)
        if not f.exists():
            raise ManifestError(f"{layer}/{name}@{version} is pinned but {f.name} does not exist")
        if not pin:
            raise ManifestError(f"{layer}/{name}@{version} has no sha256 pin (run `pin`)")
        if _sha(f.read_bytes()) != pin:
            raise ManifestError(
                f"{layer}/{name}@{version} was edited after being pinned: published versions are "
                "immutable, create a new version instead"
            )


class Composer:
    def __init__(self, manifest_path: Path = MANIFEST_PATH) -> None:
        verify_manifest(manifest_path)
        self._manifest = load_manifest(manifest_path)
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False
        )

    def _layer(self, layer: str, name: str, **variables: str) -> tuple[str, str]:
        try:
            version = self._manifest[layer][name]["version"]
        except KeyError as exc:
            raise ManifestError(f"{layer}/{name} is not pinned in the manifest") from exc
        source = _template_path(layer, name, version).read_text(encoding="utf-8")
        return self._env.from_string(source).render(**variables).rstrip("\n"), (
            f"{layer}/{name}@{version}"
        )

    def compose(self, session: SessionPrompt, fragments: str | Sequence[str]) -> ComposedPrompt:
        """`fragments`: the already-rendered `<fragmento>` blocks (app.rag.expansion)."""
        raw = fragments if isinstance(fragments, str) else "\n\n".join(fragments)
        body = _WRAPPER.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", raw)  # cannot close layer 5
        parts: list[str] = []
        used: list[str] = []
        for layer, name, vars_ in (
            ("core", "anti_hallucination", {}),
            ("workflow", session.workflow, {}),
            ("persona", session.persona, {}),
            (
                "session",
                "session",
                {
                    "jurisdiction_display": session.jurisdiction_display,
                    "language_display": LANGUAGE_DISPLAY[session.language],
                },
            ),
        ):
            text, ident = self._layer(layer, name, **vars_)
            parts.append(text)
            used.append(ident)
        parts.append(f"<contexto_recuperado>\n{body}\n</contexto_recuperado>")  # layer 5: a value
        system = "\n\n".join(parts) + "\n"
        return ComposedPrompt(
            system=system, versions=tuple(used), sha256=_sha(system.encode("utf-8"))
        )


def pin(path: Path = MANIFEST_PATH) -> list[str]:
    """Fill the sha256 of entries that have none. NEVER rewrites an existing pin."""
    raw = path.read_text(encoding="utf-8")
    header = "".join(ln + "\n" for ln in raw.splitlines() if ln.startswith("#"))
    data = yaml.safe_load(raw)
    added: list[str] = []
    for layer, items in data["layers"].items():
        for name, e in items.items():
            if not e.get("sha256"):
                f = _template_path(layer, name, e["version"])
                e["sha256"] = _sha(f.read_bytes())
                added.append(f"{layer}/{name}@{e['version']}")
    if added:
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        path.write_text(header + body, encoding="utf-8", newline="\n")
    return added


def main(argv: list[str] | None = None) -> int:
    cmd = (argv if argv is not None else sys.argv[1:])[:1]
    if cmd == ["pin"]:
        sys.stdout.write(f"pinned: {pin()}\n")
        return 0
    if cmd == ["verify"]:
        verify_manifest()
        sys.stdout.write("manifest ok\n")
        return 0
    sys.stderr.write("usage: python -m app.prompts.composer pin|verify\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

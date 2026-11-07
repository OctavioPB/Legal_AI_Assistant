"""Renders the RLS policy templates in `rls_templates.sql` (multi-tenancy.md: copy the policy pair
from the template, never hand-write a policy in a migration)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

TemplateKind = Literal["corpus", "private", "append_only"]

_TEMPLATES_FILE = Path(__file__).with_name("rls_templates.sql")
_BLOCK = re.compile(r"-- @template (\w+)\n(.*?)-- @end", re.DOTALL)


@lru_cache
def _templates() -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _BLOCK.finditer(_TEMPLATES_FILE.read_text("utf-8"))}


def render(kind: TemplateKind, table: str) -> str:
    """SQL enabling+forcing RLS on `table` with the policies of `kind`."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        raise ValueError(f"unsafe table name: {table!r}")
    return _templates()[kind].replace("{table}", table)


def statements(kind: TemplateKind, table: str) -> list[str]:
    """`render` split into single statements (comment-only lines dropped)."""
    lines = [ln for ln in render(kind, table).splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]

"""Token counting. The chunker counts with the EMBEDDER's tokenizer (rag.md); until the Cohere
tokenizer is wired (WS2.T06) `ApproxCounter` over-estimates on purpose so limits err on the safe
side. Every count that gates a limit goes through this Protocol."""

from __future__ import annotations

import re
from typing import Protocol

_WORD = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ApproxCounter:
    """Words + punctuation, x1.3 (Spanish sub-word splitting). Deterministic; NOT the embedder's
    tokenizer: its `name` is stored with every chunk so a swap is a visible version change."""

    name = "approx-words-x1.3"

    def count(self, text: str) -> int:
        n = len(_WORD.findall(text))
        return 0 if n == 0 else max(1, round(n * 1.3))

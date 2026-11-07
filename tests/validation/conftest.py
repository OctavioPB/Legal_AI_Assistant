"""Pure-logic suites: no database. Overrides the autouse DB cleanup of tests/conftest.py."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_data() -> None:
    return None

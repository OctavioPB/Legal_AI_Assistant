"""The demo/presentation accounts: created idempotently, usable to log in, never a weak password."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app import demo
from app.api.auth import authenticate
from app.settings import get_settings


@pytest.fixture
def on_test_db(monkeypatch: pytest.MonkeyPatch, migrated_db: str) -> Iterator[None]:
    """`ensure_demo` reads the global settings: point them at the test database, never the demo one."""
    monkeypatch.setenv("LEGALAI_DB_NAME", migrated_db)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_memorable_password_is_words_and_digits_long_enough_to_be_accepted() -> None:
    seen = {demo.memorable_password() for _ in range(200)}
    assert len(seen) > 190  # random, not a fixed phrase
    for pw in seen:
        assert re.fullmatch(r"[a-z]+-[a-z]+-[a-z]+-\d{4}", pw) and len(pw) >= 10
        assert all(w in demo.WORDS for w in pw.split("-")[:3])


def test_a_presentation_account_is_created_once_and_can_log_in(  # type: ignore[no-untyped-def]
    on_test_db, app_engine: Engine, admin_engine: Engine, tmp_path: Path, capsys
) -> None:
    out = tmp_path / ".presentation-credentials"
    args = ["--slug", "presentacion", "--name", "Despacho Presentación", "--email",
            "presentacion@demo.local", "--display-name", "Lic. Presentación", "--memorable",
            "--save", str(out)]  # fmt: skip
    assert demo.main(args) == 0
    saved = dict(line.split(":", 1) for line in out.read_text(encoding="utf-8").splitlines())
    password = saved["password"].strip()
    assert password in capsys.readouterr().out  # shown once on screen, and saved to the file

    engine = app_engine  # the API's role: login goes through RLS exactly as in production
    p = authenticate(engine, "presentacion", "presentacion@demo.local", password)
    assert (p.display_name, p.role, p.preferred_language) == ("Lic. Presentación", "owner", "es-MX")
    with admin_engine.connect() as c:
        name = c.execute(text("SELECT name FROM tenants WHERE slug = 'presentacion'")).scalar_one()
    assert name == "Despacho Presentación"

    # a second run changes nothing and reveals nothing
    assert demo.main(args) == 0
    assert "already exists" in capsys.readouterr().out
    assert authenticate(engine, "presentacion", "presentacion@demo.local", password)


def test_reset_gives_a_new_password_and_the_old_one_stops_working(  # type: ignore[no-untyped-def]
    on_test_db, app_engine: Engine
) -> None:
    from app.api.auth import AuthError

    first = demo.ensure_demo("presentacion", "P", "p@demo.local", reset=False, memorable=True)
    second = demo.ensure_demo("presentacion", "P", "p@demo.local", reset=True, memorable=True)
    assert first and second and first != second
    assert authenticate(app_engine, "presentacion", "p@demo.local", second)
    with pytest.raises(AuthError):
        authenticate(app_engine, "presentacion", "p@demo.local", first)

"""Create the local demo firm and its users (idempotent).

    uv run python -m app.demo                   # firm "demo", user owner@demo.local
    uv run python -m app.demo --reset-password  # new random password for the existing user

The tenant row is written by `legalai_provisioner` (the only role that may), the user by the
application role under that tenant's RLS context. The password is random, shown ONCE and stored
only as a scrypt hash.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import create_engine, text

from app.api.auth import hash_password
from app.db.session import tenant_transaction
from app.settings import get_settings


def ensure_demo(slug: str, name: str, email: str, *, reset: bool) -> str | None:
    """Returns the new password when one was set, else None (user already existed)."""
    s = get_settings()
    prov = create_engine(s.provisioner_url())
    app = create_engine(s.app_url())
    try:
        with prov.begin() as c:
            tenant_id = c.execute(
                text(
                    "INSERT INTO tenants (slug, name) VALUES (:s, :n) "
                    "ON CONFLICT (slug) DO UPDATE SET status = 'active' RETURNING id"
                ),
                {"s": slug, "n": name},
            ).scalar_one()
        with tenant_transaction(app, tenant_id) as c:
            exists = c.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email}).first()
            if exists and not reset:
                return None
            password = secrets.token_urlsafe(12)
            digest = hash_password(password)
            if exists:
                c.execute(
                    text("UPDATE users SET password_hash = :h WHERE email = :e"),
                    {"h": digest, "e": email},
                )
            else:
                c.execute(
                    text(
                        "INSERT INTO users (tenant_id, email, display_name, role, password_hash) "
                        "VALUES (:t, :e, 'Usuario Demo', 'owner', :h)"
                    ),
                    {"t": tenant_id, "e": email, "h": digest},
                )
            return password
    finally:
        prov.dispose()
        app.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.demo")
    ap.add_argument("--slug", default="demo")
    ap.add_argument("--email", default="owner@demo.local")
    ap.add_argument("--reset-password", action="store_true")
    a = ap.parse_args(argv)
    password = ensure_demo(a.slug, "Despacho Demo", a.email, reset=a.reset_password)
    if password is None:
        sys.stdout.write(
            f"user {a.email} already exists in firm {a.slug!r} (use --reset-password)\n"
        )
    else:
        sys.stdout.write(
            f"firm:     {a.slug}\nemail:    {a.email}\npassword: {password}\n"
            "(shown once; only its hash is stored)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

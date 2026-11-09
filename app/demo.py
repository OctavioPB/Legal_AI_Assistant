"""Create the local demo firm and its users (idempotent).

    uv run python -m app.demo                   # firm "demo", user owner@demo.local
    uv run python -m app.demo --reset-password  # new random password for the existing user
    uv run python -m app.demo --slug presentacion --name "Despacho Presentación"
        --email presentacion@demo.local --display-name "Lic. Presentación" --memorable

The tenant row is written by `legalai_provisioner` (the only role that may), the user by the
application role under that tenant's RLS context. The password is random, shown ONCE and stored
only as a scrypt hash.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.api.auth import hash_password
from app.db.session import tenant_transaction
from app.settings import get_settings

# Short, unambiguous words, easy to type in front of an audience. Passphrase = 3 words + 4 digits
# (64^3 x 10^4, about 31 bits): enough for an account that only listens on 127.0.0.1.
WORDS = (
    "acta",
    "agua",
    "alba",
    "ancla",
    "arco",
    "arena",
    "aroma",
    "astro",
    "aula",
    "barco",
    "boca",
    "brisa",
    "cafe",
    "calma",
    "campo",
    "canal",
    "carta",
    "cedro",
    "cielo",
    "cima",
    "clave",
    "cobre",
    "costa",
    "duna",
    "faro",
    "fuego",
    "gema",
    "grano",
    "hoja",
    "isla",
    "jade",
    "lago",
    "lienzo",
    "llave",
    "luna",
    "mapa",
    "mar",
    "menta",
    "monte",
    "nube",
    "olivo",
    "onda",
    "palma",
    "pino",
    "plaza",
    "polen",
    "prisma",
    "puente",
    "rama",
    "rio",
    "roble",
    "rumbo",
    "sal",
    "selva",
    "sol",
    "tinta",
    "torre",
    "trigo",
    "valle",
    "vela",
    "viento",
    "volcan",
    "yunque",
    "zafiro",
)


def memorable_password(rng: secrets.SystemRandom | None = None) -> str:
    r = rng or secrets.SystemRandom()
    return "-".join(r.choice(WORDS) for _ in range(3)) + "-" + f"{r.randrange(10_000):04d}"


def ensure_demo(
    slug: str, name: str, email: str, *, reset: bool, display_name: str = "Usuario Demo",
    language: str = "es-MX", memorable: bool = False,
) -> str | None:  # fmt: skip
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
            password = memorable_password() if memorable else secrets.token_urlsafe(12)
            digest = hash_password(password)
            if exists:
                c.execute(
                    text(
                        "UPDATE users SET password_hash = :h, display_name = :d, "
                        "preferred_language = :l WHERE email = :e"
                    ),
                    {"h": digest, "e": email, "d": display_name, "l": language},
                )
            else:
                c.execute(
                    text(
                        "INSERT INTO users (tenant_id, email, display_name, role, password_hash, "
                        "preferred_language) VALUES (:t, :e, :d, 'owner', :h, :l)"
                    ),
                    {"t": tenant_id, "e": email, "h": digest, "d": display_name, "l": language},
                )
            return password
    finally:
        prov.dispose()
        app.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.demo")
    ap.add_argument("--slug", default="demo")
    ap.add_argument("--email", default="owner@demo.local")
    ap.add_argument("--name", default="Despacho Demo", help="firm name shown in the UI")
    ap.add_argument("--display-name", default="Usuario Demo")
    ap.add_argument("--lang", default="es-MX", choices=("es-MX", "en-US"))
    ap.add_argument("--reset-password", action="store_true")
    ap.add_argument(
        "--memorable", action="store_true", help="word-word-word-1234 (easy to type live)"
    )
    ap.add_argument(
        "--save", type=Path, help="write firm/email/password to this file (keep it out of git)"
    )
    a = ap.parse_args(argv)
    password = ensure_demo(
        a.slug, a.name, a.email, reset=a.reset_password, display_name=a.display_name,
        language=a.lang, memorable=a.memorable,
    )  # fmt: skip
    if password is None:
        sys.stdout.write(
            f"user {a.email} already exists in firm {a.slug!r} (use --reset-password)\n"
        )
    else:
        record = f"firm:     {a.slug}\nemail:    {a.email}\npassword: {password}\n"
        sys.stdout.write(record + "(shown once; only its hash is stored)\n")
        if a.save:
            a.save.write_text(record, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

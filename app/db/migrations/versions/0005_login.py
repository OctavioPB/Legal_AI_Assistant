"""Login support: password hashes and a narrow pre-authentication lookup of one tenant by slug.

Login happens BEFORE the tenant is known, yet every table is fail-closed without `app.tenant_id`.
Two deliberate, minimal openings, both exact-match on a login-scoped setting that the API sets
for one transaction only:
  * `tenants_login_lookup`: SELECT of the single tenant whose slug equals `app.login_slug`
    (never PUBLIC_CORPUS, never a list);
  * users are then read under the normal anchor (`app.tenant_id` = that tenant), so email stays
    unique PER TENANT and no cross-tenant existence oracle is created (ADR-010).
`users.password_hash` is a scrypt string produced by `app/api/auth.py`.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN password_hash text")
    op.execute(
        "CREATE POLICY tenants_login_lookup ON tenants FOR SELECT TO legalai_app "
        "USING (slug = nullif(current_setting('app.login_slug', true), '') "
        "AND id <> public_corpus_id())"
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

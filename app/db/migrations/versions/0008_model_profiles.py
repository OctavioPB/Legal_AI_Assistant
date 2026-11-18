"""ADR-024 — model profile per conversation, and the firm's opt-in for remote models.

* `conversations.model_profile`: closed list, fixed at creation (like jurisdiction and persona).
  Existing conversations keep answering with the local default (`gemma`).
* `tenant_model_policy`: one row per firm, tenant-private RLS. `allow_remote` is false unless the
  firm's admin turns it on (the API checks the role; `tenants` is written only by the provisioner,
  so the opt-in cannot live there). No row = remote models not allowed.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

from app.db import rls

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversations ADD COLUMN model_profile text NOT NULL DEFAULT 'gemma' "
        "CHECK (model_profile IN ('gemma','qwen','claude'))"
    )
    op.execute(
        """
        CREATE TABLE tenant_model_policy (
            tenant_id     uuid PRIMARY KEY REFERENCES tenants(id),
            allow_remote  boolean NOT NULL DEFAULT false,
            updated_by    uuid,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT tenant_model_policy_user_fk FOREIGN KEY (updated_by, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for stmt in rls.statements("private", "tenant_model_policy"):
        op.execute(stmt)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_model_policy TO legalai_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

"""ADR-028 — the key-points sheet of a matter (hoja de puntos clave).

* `matter_cheatsheets`: one row per generation. It holds client work (the model's validated sheet,
  built from the matter's conversations), so it is tenant-private and as visible as its matter:
  `private` + the RESTRICTIVE `matter_child` template (creator and members only; nothing without the
  user anchor). Deleting the matter deletes its sheets.
* A refused generation is stored too (status `refused`, the system's reason key, no model text): the
  refusal is a first-class output.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

from app.db import rls

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE matter_cheatsheets (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            matter_id       uuid NOT NULL,
            created_by      uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            language        text NOT NULL CHECK (language IN ('es','en')),
            model_profile   text NOT NULL CHECK (length(model_profile) BETWEEN 1 AND 40),
            status          text NOT NULL CHECK (status IN ('answered','refused')),
            content         text CHECK (length(content) <= 20000),
            citations       jsonb NOT NULL DEFAULT '[]'::jsonb,
            refusal_reason  text CHECK (length(refusal_reason) <= 80),
            material        jsonb NOT NULL DEFAULT '{}'::jsonb,
            request_id      uuid,
            CONSTRAINT matter_cheatsheets_answer_ck CHECK (
                (status = 'answered') = (content IS NOT NULL AND refusal_reason IS NULL)),
            CONSTRAINT matter_cheatsheets_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT matter_cheatsheets_user_fk FOREIGN KEY (created_by, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX matter_cheatsheets_matter_idx ON matter_cheatsheets "
        "(tenant_id, matter_id, created_at DESC)"
    )
    kinds: tuple[rls.TemplateKind, ...] = ("private", "matter_child")
    for kind in kinds:
        for stmt in rls.statements(kind, "matter_cheatsheets"):
            op.execute(stmt)
    op.execute("GRANT SELECT, INSERT, DELETE ON matter_cheatsheets TO legalai_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

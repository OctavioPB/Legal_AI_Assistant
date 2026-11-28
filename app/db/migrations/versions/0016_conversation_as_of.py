"""ADR-050 — a conversation may fix the date of the facts: the law is read as of that date.

`conversations.as_of_date` is fixed at creation, like jurisdiction and persona (ADR-016): changing it is
a new conversation, never an inference from text. NULL keeps today's behaviour (current text). The
table's RLS policies are unchanged (a new column of an already private table).

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversations ADD COLUMN as_of_date date
            CHECK (as_of_date IS NULL OR as_of_date >= DATE '1900-01-01');
        ALTER TABLE conversations ADD CONSTRAINT conversations_as_of_not_historical
            CHECK (as_of_date IS NULL OR NOT include_historical);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

"""ADR-038 (D) — units of a firm document where an amount's figure and its words disagree.

`client_files.amount_mismatch_units` holds the cite_ids of those units (ids only, never their text,
like the OCR fields of 0013). NULL: the document was processed before this check existed.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE client_files ADD COLUMN amount_mismatch_units text[]")


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

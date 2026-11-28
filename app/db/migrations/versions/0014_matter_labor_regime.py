"""ADR-031 — a matter may declare its labour regime, from a closed list.

`matters.labor_regime`: NULL (not declared), `lft:ordinaria`, `lft:colectivo` or
`lft:especial:<Capítulo of Título Sexto>` (e.g. `lft:especial:VIII`, `lft:especial:XIII-BIS`). The
API validates against `app.corpus.regimes.CASE_REGIMES`; the CHECK keeps the shape even if a client
bypasses it. The table keeps its RLS (0007). No content: a code.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE matters
            ADD COLUMN labor_regime text
                CHECK (labor_regime ~ '^lft:(ordinaria|colectivo|especial:[IVXLC]+(-BIS)?)$')
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

"""ADR-030 — OCR of scanned pages of a firm's PDF: what was read by OCR is recorded per upload.

* `ocr_pages`: pages read by OCR and kept as citable text.
* `ocr_excluded_pages`: pages read by OCR whose mean word confidence was under the threshold; their
  text was not stored, and the interface asks for a better scan.
* `ocr_confidence`: word-weighted mean confidence (0-1) of the kept pages.
* `ocr_engine`: engine, version and language data used (provenance, like a pinned model).
* `force_ocr`: the uploader asked to read every page as scanned (a legible but wrong text layer).
No content is added: page numbers and numbers only. The table keeps its RLS (`private` +
`matter_scoped`, 0009).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE client_files
            ADD COLUMN ocr_pages integer[] NOT NULL DEFAULT '{}',
            ADD COLUMN ocr_excluded_pages integer[] NOT NULL DEFAULT '{}',
            ADD COLUMN ocr_confidence real CHECK (ocr_confidence BETWEEN 0 AND 1),
            ADD COLUMN ocr_engine text CHECK (length(ocr_engine) <= 80),
            ADD COLUMN force_ocr boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

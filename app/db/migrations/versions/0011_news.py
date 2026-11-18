"""ADR-027 — the legal news feed.

* `news_items`: the feed (`in_feed`, at most `news_feed_size` rows) and the items that left it, one
  list shared by every firm, so it lives under the public corpus tenant: written only by
  `corpus_ingestor` (the news pipeline), read by every tenant. Public rows are never deleted (the
  corpus template has no delete policy for the ingestor): an item that leaves the feed is retired.
  Each row keeps the source's own title and short summary, the link, the dates, the legal terms that
  matched and the score (fixed rules, no model).
* `source_checks.outcome` gains `collected` (a news source was read).

Revision ID: 0011
Revises: 0010
"""

from alembic import op

from app.db import rls

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE news_items (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES tenants(id),
            source_id      text NOT NULL CHECK (source_id ~ '^[a-z][a-z0-9_]{1,40}$'),
            kind           text NOT NULL CHECK (kind IN
                             ('reform','new_law','recent_reform','court','press')),
            title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 600),
            url            text NOT NULL CHECK ((url ~ '^https://' OR url ~ '^/[a-z]')
                                                AND length(url) <= 500),
            outlet         text NOT NULL CHECK (length(outlet) BETWEEN 1 AND 200),
            published_at   timestamptz,
            first_seen_at  timestamptz NOT NULL DEFAULT now(),
            excerpt        text CHECK (length(excerpt) <= 600),
            facts          jsonb NOT NULL DEFAULT '{}'::jsonb,
            terms          text[] NOT NULL DEFAULT '{}',
            score          real NOT NULL,
            official       boolean NOT NULL,
            refreshed_at   timestamptz NOT NULL DEFAULT now(),
            in_feed        boolean NOT NULL DEFAULT true,
            CONSTRAINT news_items_url_uq UNIQUE (tenant_id, url)
        )
        """
    )
    op.execute("CREATE INDEX news_items_feed_idx ON news_items (tenant_id, in_feed, score DESC)")
    for stmt in rls.statements("corpus", "news_items"):
        op.execute(stmt)
    # Same grants as the other corpus tables: RLS keeps the app role off PUBLIC_CORPUS rows.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON news_items TO legalai_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON news_items TO corpus_ingestor")

    op.execute("ALTER TABLE source_checks DROP CONSTRAINT source_checks_outcome_check")
    op.execute(
        "ALTER TABLE source_checks ADD CONSTRAINT source_checks_outcome_check CHECK (outcome IN "
        "('unchanged','ingested','failed','blocked','skipped','indexed','collected'))"
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

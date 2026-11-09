"""ADR-016 — persisted conversations for the three workspaces (Conversar, Investigación, Argumentar).

`conversations` fixes the session context at creation (catalog keys only: jurisdiction, persona,
language); changing it is a new conversation. A case (`kind = 'case'`) also holds the user's free text
(facts, claim) and a side from a closed list. `messages` hold client content: tenant-private RLS,
composite FKs carrying tenant_id, ON DELETE CASCADE from the conversation (a user may delete one; the
audit log keeps its own append-only copy, ADR-013).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

from app.db import rls

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL,
            user_id             uuid NOT NULL,
            kind                text NOT NULL CHECK (kind IN ('chat','research','case')),
            title               text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            language            text NOT NULL CHECK (language IN ('es','en')),
            jurisdiction_id     integer NOT NULL REFERENCES jurisdictions(id),
            persona             text NOT NULL REFERENCES personas(code),
            include_historical  boolean NOT NULL DEFAULT false,
            case_side           text CHECK (case_side IN
                                  ('actor','demandado','defensa','acusacion','tercero')),
            case_facts          text CHECK (length(case_facts) <= 8000),
            case_claim          text CHECK (length(case_claim) <= 8000),
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT conversations_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT conversations_user_fk FOREIGN KEY (user_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT conversations_case_fields CHECK (
                (kind = 'case') = (case_side IS NOT NULL AND case_facts IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE messages (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL,
            conversation_id  uuid NOT NULL,
            seq              integer NOT NULL CHECK (seq > 0),
            role             text NOT NULL CHECK (role IN ('user','assistant')),
            action           text NOT NULL CHECK (action IN ('ask','research','attack','strengthen')),
            content          text NOT NULL,
            status           text CHECK (status IN ('answered','refused')),
            refusal_reason   text,
            citations        jsonb NOT NULL DEFAULT '[]'::jsonb,
            request_id       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT messages_conversation_fk FOREIGN KEY (conversation_id, tenant_id)
                REFERENCES conversations (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT messages_seq_uq UNIQUE (conversation_id, seq),
            CONSTRAINT messages_assistant_status CHECK ((role = 'assistant') = (status IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX conversations_user_idx ON conversations (tenant_id, user_id, kind, updated_at DESC)"
    )
    for table in ("conversations", "messages"):
        for stmt in rls.statements("private", table):
            op.execute(stmt)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages TO legalai_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")

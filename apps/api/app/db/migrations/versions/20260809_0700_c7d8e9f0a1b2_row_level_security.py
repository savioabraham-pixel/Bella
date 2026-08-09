"""Row-Level Security and audit-log immutability

Application code always filters by user_id. This is the backstop for the day it does not:
a query that forgets its WHERE clause returns zero rows instead of another family
member's conversations.

Three things make this actually work rather than decoratively:

  * the runtime role (bella_app) is NOT the table owner and is NOT a superuser — both
    bypass RLS entirely, so an app connecting as the owner would have policies that
    never fire. Ownership sits with bella_migrator; see infra/docker/postgres/initdb.
  * `app.user_id` is set with `set_config(..., true)`, making it transaction-local. It
    cannot leak across pooled connections, because a pooled connection is only reused
    after its transaction ends.
  * FORCE is explicitly disabled. `DISABLE ROW LEVEL SECURITY` does not reset the FORCE
    flag, so a database on which it was ever set would keep it and lock the owner — and
    therefore migrations, seeding, exports and erasure jobs — out of every table.

Revision ID: c7d8e9f0a1b2
Revises: f30eb4e95086
Created: 2026-08-09

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "8044448e27c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bella_app"

# Tables whose rows belong to exactly one user, keyed by the column that says which.
OWNED_TABLES: dict[str, str] = {
    "profiles": "user_id",
    "relationships": "user_id",
    "sessions": "user_id",
    "user_quotas": "user_id",
    "threads": "user_id",
    "messages": "user_id",
    "message_audio": "user_id",
    "memories": "user_id",
    "files": "user_id",
    "file_chunks": "user_id",
    "tool_invocations": "user_id",
    "consents": "user_id",
    "data_requests": "user_id",
}

# Join tables have no user_id of their own; they inherit ownership from their parent.
DERIVED_POLICIES: dict[str, str] = {
    "message_attachments": """
        EXISTS (
            SELECT 1 FROM messages m
            WHERE m.id = message_attachments.message_id
              AND m.user_id = current_setting('app.user_id', true)::uuid
        )
    """,
}


def upgrade() -> None:
    for table, column in OWNED_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_owner ON {table}
                FOR ALL
                TO {APP_ROLE}
                USING ({column} = current_setting('app.user_id', true)::uuid)
                WITH CHECK ({column} = current_setting('app.user_id', true)::uuid)
            """
        )

    for table, predicate in DERIVED_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_owner ON {table}
                FOR ALL
                TO {APP_ROLE}
                USING ({predicate})
                WITH CHECK ({predicate})
            """
        )

    # `users` is readable by its owner, but auth must also resolve a row by firebase_uid
    # *before* app.user_id can be known. That lookup is the one path that legitimately
    # runs unscoped, so it uses a dedicated SECURITY DEFINER function rather than a hole
    # in the policy.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY users_self ON users
            FOR ALL
            TO {APP_ROLE}
            USING (id = current_setting('app.user_id', true)::uuid)
            WITH CHECK (id = current_setting('app.user_id', true)::uuid)
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth_lookup_user(p_firebase_uid text)
        RETURNS TABLE (id uuid, status text, role text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT u.id, u.status, u.role
            FROM users u
            WHERE u.firebase_uid = p_firebase_uid
              AND u.deleted_at IS NULL
        $$;
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO {APP_ROLE}")

    # The audit log is append-only. Revoking UPDATE and DELETE means a compromised
    # application role cannot erase its own tracks.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {APP_ROLE}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (attempted %)', TG_OP;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_mutation
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
        """
    )

    # Feature flags and prompt versions are operational config: readable by the app,
    # writable only through the admin path, which connects as the migrator role.
    for table in ("feature_flags", "prompt_versions"):
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {table} FROM {APP_ROLE}")


def downgrade() -> None:
    for table in ("feature_flags", "prompt_versions"):
        op.execute(f"GRANT INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")

    op.execute("DROP TRIGGER IF EXISTS audit_log_no_mutation ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    op.execute(f"GRANT UPDATE, DELETE ON audit_log TO {APP_ROLE}")

    op.execute("DROP FUNCTION IF EXISTS auth_lookup_user(text)")
    op.execute("DROP POLICY IF EXISTS users_self ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")

    for table in DERIVED_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {table}_owner ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in OWNED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_owner ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

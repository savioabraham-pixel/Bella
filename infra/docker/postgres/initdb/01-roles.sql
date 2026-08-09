-- Roles.
--
-- The application does NOT connect as a superuser. This matters for Row-Level Security:
-- superusers and table owners bypass RLS entirely, so if the app connected as the owner
-- every policy in the schema would be decorative.
--
-- Two roles:
--   bella_migrator  owns the schema, runs Alembic, has DDL rights
--   bella_app       the runtime role — DML only, and RLS applies to it
--
-- Passwords here are local-only. Cloud SQL uses IAM authentication instead.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bella_migrator') THEN
        CREATE ROLE bella_migrator LOGIN PASSWORD 'bella_migrator_local';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bella_app') THEN
        CREATE ROLE bella_app LOGIN PASSWORD 'bella_app_local';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE bella TO bella_migrator, bella_app;

\connect bella

GRANT USAGE, CREATE ON SCHEMA public TO bella_migrator;
GRANT USAGE ON SCHEMA public TO bella_app;

-- Whatever bella_migrator creates from now on is automatically usable by bella_app,
-- so a new table does not need a follow-up GRANT to become readable.
ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bella_app;

ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO bella_app;

ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO bella_app;

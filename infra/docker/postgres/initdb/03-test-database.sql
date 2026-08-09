-- A separate database for the integration test suite, so `make test` never touches
-- development data and the two can run concurrently.
--
-- The privilege setup must mirror `bella` exactly. The integration suite connects as
-- bella_app specifically so that Row-Level Security is exercised; if this database
-- granted different privileges, the isolation tests would be testing a fiction.

\set ON_ERROR_STOP on

CREATE DATABASE bella_test OWNER bella;

\connect bella_test

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

GRANT CONNECT ON DATABASE bella_test TO bella_migrator, bella_app;
GRANT USAGE, CREATE ON SCHEMA public TO bella_migrator;
GRANT USAGE ON SCHEMA public TO bella_app;

-- Default privileges are per-database, so they must be declared here as well as in
-- 01-roles.sql. Without this, every table the migrator creates in bella_test is
-- unreadable by bella_app and the suite fails with "permission denied".
ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bella_app;

ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO bella_app;

ALTER DEFAULT PRIVILEGES FOR ROLE bella_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO bella_app;

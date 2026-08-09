-- Extensions.
--
-- Alembic migration a1b2c3d4e5f6 also creates these, because Cloud SQL has no initdb
-- hook. Creating them here as well means a local database is usable before migrations
-- have ever run — useful for `psql` poking and for the test harness.

\set ON_ERROR_STOP on
\connect bella

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

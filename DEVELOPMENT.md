# Development

Everything runs in Docker. You do **not** need Python, Node, pnpm or uv on your machine —
only Docker with Compose v2.

```bash
git clone <repo> && cd Bella
make bootstrap
```

That copies `.env.example` → `.env`, builds every image, starts the stack, and applies
migrations. When it finishes:

| | |
|---|---|
| Web | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| API health | http://localhost:8000/health/ready |
| Mail catcher | http://localhost:8025 |
| Postgres | `localhost:5432` (loopback only) |
| Redis | `localhost:6379` (loopback only) |

`make help` lists every target.

---

## Layout

```
apps/
  api/          FastAPI application + Alembic migrations + tests
  web/          Next.js 15 application
infra/
  docker/
    api/        API image        (base → deps → dev → prod)
    worker/     Worker image     (adds poppler/tesseract; no HTTP surface)
    web/        Web image        (deps → dev → builder → prod, standalone output)
    postgres/   pgvector + roles, privileges and the test database
  terraform/    (Phase 1 of the roadmap — not yet populated)
packages/
  api-client/   generated from the OpenAPI schema; never hand-edited
docs/           architecture, data model, API contract, roadmap
legacy/         the original single-file app, frozen as a behaviour reference
```

## Services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `bella-postgres:dev` | Postgres 17 + pgvector, roles, test database |
| `redis` | `redis:7.4-alpine` | Cache, rate limits, idempotency, SSE replay |
| `api` | `bella-api:dev` | FastAPI, hot reload |
| `worker` | `bella-worker:dev` | ARQ background jobs |
| `web` | `bella-web:dev` | Next.js dev server |
| `mailpit` | `axllent/mailpit` | Catches all outbound mail |
| `migrate` | `bella-api:dev` | One-shot Alembic runner (`tools` profile) |

Each image is built from its own Dockerfile and can be built independently — the worker
deliberately does **not** derive `FROM bella-api`, so the two build in parallel in CI.

---

## Database roles — read this before touching data access

There are three roles, and the distinction is load-bearing:

| Role | Used by | Rights | RLS |
|---|---|---|---|
| `bella` | superuser, local only | everything | bypassed |
| `bella_migrator` | Alembic, seeds, admin jobs | DDL + DML, owns the tables | bypassed (owner) |
| `bella_app` | **the API and worker at runtime** | DML only | **enforced** |

Row-Level Security does not apply to superusers or table owners. If the application
connected as the owner, every policy in the schema would be decorative. That is why the
runtime `DATABASE_URL` uses `bella_app` and migrations use `bella_migrator`.

Application code sets the current user per transaction:

```python
async with session_for_user(user_id) as session:
    ...  # SET LOCAL app.user_id, so RLS policies resolve
```

A query that forgets its `WHERE user_id = …` returns **zero rows**, not another user's
data. `apps/api/tests/test_security_isolation.py` asserts this, and those tests fail if
RLS is removed — verified by disabling a policy and watching five of them go red.

---

## Migrations

```bash
make migrate                       # apply to head
make migrate-auto M="add widgets"  # autogenerate from model changes
make migrate-down                  # roll back one
make migrate-history               # what is applied
```

Rules:

- **Forward-only in production**, expand → migrate → contract. Add the column, backfill,
  dual-write, switch reads, drop the old column in a *later* release.
- Autogenerate is a starting point, not an output. Read the generated file — it will miss
  data migrations and it renders `server_default` changes conservatively.
- Every column that has a Python-side `default=` also needs `server_default=` if raw SQL,
  seeds or data migrations will ever insert into it. Python defaults exist only in the ORM.
- pgvector types are rendered by a `render_item` hook in `env.py`; it emits the
  `import pgvector.sqlalchemy` that autogenerate otherwise omits.

---

## Testing

```bash
make test          # api + web
make test-api      # migrates bella_test, then runs pytest as bella_app
make test-web
make test-e2e
```

`test-api` connects as the **runtime** role on purpose. Running the suite as the owner
would make every isolation assertion pass vacuously.

Markers: `integration` (needs Postgres and Redis), `security` (cross-tenant isolation).

---

## Quality gates

```bash
make lint        # ruff check + ruff format --check + eslint + prettier
make typecheck   # mypy --strict + tsc --noEmit
make check       # everything CI runs
```

Both are clean as of the last commit and CI blocks on them.

Two deliberate configuration choices, recorded so they are not "fixed" later:

- **Ruff's `TCH` rules are disabled.** They assume annotations are never needed at
  runtime. FastAPI resolves them for dependency injection, SQLAlchemy for mapper
  configuration, and Pydantic for model building — applying those fixes breaks all three
  at import time.
- **One `type: ignore` in `app/core/redis.py`.** `aclose()` exists at runtime in
  redis ≥ 5.0.1 and is the documented replacement for the deprecated `close()`; it is
  simply missing from the shipped annotations. The runtime code is correct.

---

## Gotchas worth knowing

**Changing a Postgres init script requires a rebuild *and* a volume reset.** Scripts in
`infra/docker/postgres/initdb/` are baked into the image by `COPY` and only run against an
empty data directory:

```bash
docker compose down -v && docker compose build postgres && make bootstrap
```

**`ALTER TABLE … DISABLE ROW LEVEL SECURITY` does not clear the `FORCE` flag.** A database
on which `FORCE` was ever set keeps it, and re-enabling RLS then locks the *owner* out of
its own tables — which breaks migrations, seeding, exports and erasure jobs. The RLS
migration sets `NO FORCE` explicitly so it is idempotent regardless of prior state.

**Tooling containers run as your host uid.** `make migrate-auto` and `make format` write
into the bind mount; without this they would leave root-owned files. Long-running services
keep their unprivileged in-image user.

**Async test fixtures must not share a connection pool.** pytest-asyncio gives each test
its own event loop, and a pooled asyncpg connection cannot cross loops. Test engines use
`NullPool` and are function-scoped.

**`pydantic-settings` JSON-decodes list fields from the environment before validators
run.** `cors_origins` and `trusted_hosts` are annotated `NoDecode` so a plain
comma-separated value works in compose files; a JSON array is still accepted.

---

## Adding an endpoint

1. Model changes in `app/db/models/<domain>.py`; add the table to `RLS_TABLES` in
   `app/db/models/__init__.py` if it holds user data.
2. `make migrate-auto M="…"`, then read the generated migration.
3. Add an RLS policy for any new user-owned table — the
   `test_every_user_owned_table_has_rls_enabled` test fails until you do.
4. Router in `app/modules/<domain>/router.py`, Pydantic schemas in the same module.
5. Mount it in `app/api/v1/router.py`.
6. Tests, including an isolation test if the resource is user-owned.
7. `make openapi` to refresh the schema the TypeScript client is generated from.

---

## Current state

Phase 1 of [docs/09-migration-roadmap.md](docs/09-migration-roadmap.md) is complete:
containerised stack, configuration, structured logging with PII redaction, error envelope,
request IDs, health and readiness probes, the full data model with RLS, and CI.

The API surface itself is Phase 2 onward — `app/api/v1/router.py` lists what lands when.

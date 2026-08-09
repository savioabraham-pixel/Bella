# bella-api

FastAPI backend for Bella. Runs as two processes from one codebase: the HTTP API and the
ARQ worker.

```
app/
├── main.py          ASGI factory, middleware chain, lifespan
├── core/            config, logging, errors, middleware, redis
├── db/              declarative base, session/RLS scoping, models, migrations
├── modules/         one package per domain, each owning its routes and schemas
├── api/v1/          the versioned surface — every router is mounted here
└── workers/         ARQ task definitions and schedule
```

Everything runs through Docker from the repository root; see
[../../DEVELOPMENT.md](../../DEVELOPMENT.md).

```bash
make up            # start
make migrate       # apply migrations
make test-api      # suite, against the isolated test database
make lint          # ruff
make typecheck     # mypy --strict
```

## Two things to know before changing data access

**The runtime role is not the table owner.** Postgres exempts superusers and table owners
from Row-Level Security, so the API connects as `bella_app` (DML only) while migrations
run as `bella_migrator`. Connecting the application as the owner would make every policy
in the schema decorative.

**Scope the session, always.** `session_for_user()` sets `app.user_id` transaction-locally,
which is what the policies read. A query that omits its `WHERE user_id = …` then returns
zero rows rather than another user's data — asserted in `tests/test_security_isolation.py`,
and those tests fail if a policy is removed.

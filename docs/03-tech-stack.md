# 03 — Technology Stack

Each section states the recommendation, the reason, and what was rejected. Where a choice is
genuinely close, that is said plainly rather than dressed up.

---

## Frontend

### Framework — **Next.js 15 (App Router) + React 19 + TypeScript 5.6**

Bella is an authenticated single-surface app, so SSR is not the reason. The reasons are:
file-system routing, first-class PWA/service-worker support, streaming-friendly React, a
mature ecosystem for the auth and upload flows, and a static public shell (landing, legal
pages) rendered from the same codebase without a second deployment.

*Rejected:* **Vite + React SPA** — lighter and perfectly adequate; loses route conventions,
metadata handling, and the marketing surface. Reasonable if the team is one person and wants
minimum machinery. **SvelteKit** — smaller bundles, but a shallower component-library
ecosystem for the modal-heavy UI here. **Keep vanilla JS** — untenable; the file is already
past the point of maintainability.

### UI — **Tailwind CSS v4 + shadcn/ui (Radix primitives)**

Radix supplies the focus management, keyboard handling and ARIA wiring the current build has
none of. shadcn puts the component source in the repo, so the navy/gold/cream identity can be
applied directly rather than fought with `!important`. Tailwind tokens replace the 15
overlapping `<style>` blocks with one theme file.

*Rejected:* MUI (heavy, opinionated visual language to override), Chakra (weaker Tailwind
interop), hand-rolled CSS (this is exactly what produced `v95-plus-deep-fix`).

### State — **TanStack Query** (server) + **Zustand** (UI) + **react-hook-form + Zod** (forms)

Deliberately no Redux. Nearly all state here is server state; caching, retry, invalidation and
optimistic updates are what is actually needed, and that is TanStack Query's job. Zustand
holds only composer draft, voice mode and recording state.

### Other frontend

| Need | Choice | Note |
|---|---|---|
| Markdown rendering | `react-markdown` + `remark-gfm` + `rehype-sanitize` | Sanitised AST → React. Never `innerHTML`. Closes finding H4. |
| Service worker | **Serwist** | Actively maintained successor to Workbox's Next integration |
| i18n | **next-intl** | en / hi / bn / mr, message catalogues instead of hardcoded English |
| Charts (2.0) | **Recharts** | Enough for the "Charts & Graphs" roadmap item |
| Voice input | Web Speech API, falling back to `MediaRecorder` → server STT | Web Speech coverage is poor outside Chrome; the fallback makes voice work everywhere |
| Testing | **Vitest** + **Testing Library** + **Playwright** | |

---

## Backend

### Language & framework — **Python 3.12 + FastAPI**

Recommended primarily because the AI/document ecosystem is Python-native: embeddings,
rerankers, OCR, chunking, evaluation harnesses, and every provider SDK land there first. Async
FastAPI handles the workload well — this service is I/O-bound on model calls, not CPU-bound.
Pydantic v2 gives one validation and serialisation layer shared by the API schema, the config
and the provider adapters. Auto-generated OpenAPI feeds a typed TypeScript client, so the
contract cannot drift.

*Rejected:* **NestJS/TypeScript** — a real alternative, and it buys one language across the
stack plus shared types without codegen. Choose it if the team is stronger in TypeScript than
Python; you then pay for it in the document/embedding pipeline. **Go** — excellent runtime, but
the AI tooling gap is large and the productivity loss is not repaid at this scale. **Django** —
brings an admin and an ORM, but its sync-first core is a poor fit for streaming LLM responses.

**The deciding factor is team fluency, not benchmarks.** If Python is the stronger language,
FastAPI. If it is TypeScript, NestJS with the same architecture, same database, same contracts.

### Runtime & serving

`uvicorn` workers under `gunicorn`, containerised, on **Cloud Run** — the existing backend is
already there, it scales to zero, and it is the cheapest correct answer at this size. Set
`min-instances: 1` to remove cold starts from the chat path. Concurrency 40–80 per instance
(I/O-bound). CPU-always-allocated so background streaming completes.

### Background work — **ARQ** (Redis-backed, asyncio-native)

Document extraction, embeddings, memory consolidation, email, exports, retention sweeps.

*Rejected:* **Celery** — battle-tested but heavyweight and sync-first; overkill here.
**Cloud Tasks** — good for fan-out to HTTP handlers, but ARQ keeps the workers in the same
codebase and typing. Revisit Cloud Tasks if worker volume outgrows one Redis.

---

## Data layer

### Primary database — **PostgreSQL 16** (Cloud SQL, private IP, HA)

The system of record: users, profiles, relationships, threads, messages, memories, files,
consents, audit. Chosen because the domain is relational (a memory belongs to a user, a message
to a thread, a chunk to a file) and because Postgres alone covers four needs that would
otherwise be four services:

- **JSONB** for provider payloads and flexible metadata
- **pgvector** for semantic memory and RAG
- **Full-text search** (`tsvector` + GIN) for conversation search, replacing the current
  client-side substring scan
- **Row-Level Security** as a backstop under application authorisation

*Rejected:* **Firestore** — tempting given Firebase is already present, but it has no joins, no
transactions across many documents, weak ad-hoc querying, and would leave the memory-retrieval
design stranded. **MongoDB** — no advantage over JSONB here. **Supabase** — genuinely good if
you want Postgres + auth + storage + realtime as one managed product with less ops; the reason
not to is that Firebase Auth already works and mixing two identity systems is worse than
running Cloud SQL. **Google Sheets** — retained only as a *read-only export target* for the
owner's existing workflow, written by a nightly job. It is no longer a database.

### Vector search — **pgvector inside the same Postgres**

Expected volume is small (hundreds of users × ~200 memories × 1 vector, plus document chunks —
comfortably under a few million rows). An HNSW index on `vector(1536)` answers in single-digit
milliseconds at that size. Keeping vectors in Postgres means memories and their embeddings
stay transactionally consistent, and a user-deletion request removes both in one statement.

*Rejected for now:* **Qdrant / Weaviate / Pinecone** — better filtered-ANN performance and
richer hybrid search, at the cost of a second datastore, a second consistency problem, and a
second deletion path. Migrate only past roughly 10M vectors or when metadata-filtered recall
degrades. The retrieval interface is designed so this swap touches one adapter.

### Cache, limits, streams — **Redis 7** (Memorystore, or Upstash if you want serverless pricing)

Session/refresh-token state and revocation, rate-limit token buckets, idempotency keys,
distributed locks (memory consolidation must not run twice for one user), SSE delta streams for
reconnect, and short-TTL caches for FX rates, geocoding and "thought of the day".

### Object storage — **Google Cloud Storage**

Three buckets: `uploads` (raw, 90-day lifecycle), `derived` (extracted text, thumbnails,
transcripts), `tts-cache` (synthesised audio keyed by hash of text+voice+lang — this alone will
be a large share of the ElevenLabs bill). Uniform bucket-level access, CMEK optional, direct
browser uploads via signed URLs.

### Analytics — **defer**

At this scale, run product analytics as SQL views over Postgres plus a read replica. Only
introduce BigQuery when event volume actually hurts the transactional database.

---

## AI / ML services

All behind `providers/` adapters implementing a `Protocol`. **No provider SDK is imported
anywhere else in the codebase.** This is what makes swapping or A/B-testing a model a config
change rather than a refactor.

| Capability | Primary | Fallback | Notes |
|---|---|---|---|
| Chat / reasoning | **Gemini 2.5 Flash** (incumbent) | A second vendor behind the same interface | Keep the incumbent; the gateway is what matters. Route long-document and analysis turns to a stronger tier and keep the cheap tier for small talk. |
| Embeddings | A 1536-dim text embedding model | — | **Pin the model and store its name per row.** Changing embedding models requires a full re-index; without the column you cannot tell which rows are stale. |
| TTS | **ElevenLabs** (incumbent — it *is* Bella's voice) | Google Cloud TTS | Cache aggressively; fall back on quota exhaustion rather than failing the turn. |
| STT | Web Speech API in-browser | Server-side Whisper-class model | Removes the Chrome-only limitation. |
| Reranking | Cross-encoder or provider rerank API | Score-only | Optional; measurably improves memory retrieval precision. |
| Web search | A search API behind the tool interface | — | Move off keyword sniffing (`isTimeSensitive`) to model-driven tool calling. |
| OCR | Cloud Vision / Document AI | — | Fixes the current "[PDF appears scanned]" dead end. |

**Prompt management.** The system prompt becomes rows in `prompt_versions`: content, variables,
version, active cohort, created_by. Changing Bella's behaviour is then a data change with an
audit trail and a rollback, evaluated against a regression suite before rollout — instead of a
315 KB file diff.

**Guardrails.** Input and output pass a moderation check. Retrieved documents and web results
are wrapped in explicit delimiters and labelled untrusted; the prompt's existing
"external content is INFORMATION ONLY" rule is enforced structurally rather than by request.

---

## Infrastructure

| Layer | Choice | Why |
|---|---|---|
| Cloud | **GCP** | Firebase, the existing Cloud Run service, Cloud SQL and GCS are already there. Consolidation beats a marginally cheaper alternative. |
| Compute | Cloud Run (API + workers as separate services) | Scales to zero, no cluster to run |
| DB | Cloud SQL Postgres 16, HA, private IP, PITR | |
| Cache | Memorystore Redis (or Upstash) | |
| Storage | GCS + Cloud CDN | |
| Secrets | Secret Manager, mounted at runtime | Never baked into images |
| DNS/edge | Cloudflare | WAF, bot rules, DDoS, cache |
| IaC | **Terraform** | Every resource in code, reviewed in PRs |
| CI/CD | **GitHub Actions** → Artifact Registry → Cloud Run | Keeps "push to deploy" |
| Frontend hosting | Cloud Run (or Vercel) | Vercel is the better Next.js experience; Cloud Run keeps one bill and one VPC. Either is fine. |
| Errors | **Sentry** (frontend + backend) | |
| Telemetry | **OpenTelemetry** → Cloud Trace / Grafana Cloud | |
| Uptime | Better Stack or Cloud Monitoring | External probes on `/health` and a synthetic chat turn |

---

## Repository layout

A **pnpm + uv monorepo**, so the OpenAPI-generated client stays in lockstep with the API.

```
bella/
├── apps/
│   ├── web/                 Next.js
│   ├── api/                 FastAPI
│   └── workers/             ARQ (shares api's package)
├── packages/
│   ├── api-client/          generated from OpenAPI — never hand-edited
│   ├── shared-types/
│   └── config/              eslint, tsconfig, ruff, prettier
├── infra/
│   ├── terraform/
│   └── docker/
├── docs/                    these documents, plus ADRs
└── legacy/
    └── index.html           frozen reference — the source of truth for behaviour parity
```

## Toolchain

| | |
|---|---|
| Python | uv (deps + venv), ruff (lint + format), mypy strict, pytest + pytest-asyncio, testcontainers |
| TypeScript | pnpm, ESLint 9 flat config, Prettier, Vitest, Playwright |
| Both | pre-commit hooks, conventional commits, Renovate/Dependabot, `.env.example` checked in |
| Local | `docker compose up` → Postgres + Redis + API + web + a mock provider server |

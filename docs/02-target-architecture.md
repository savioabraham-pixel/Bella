# 02 — Target Architecture

## 1. Principles

These five constraints drive every decision that follows.

1. **The server is the only authority.** The browser never holds a system prompt, a profile, a
   quota, or an authorisation decision. Every request carries a verified identity and the
   server derives what that identity may see.
2. **Postgres is the system of record.** Everything else — Redis, the vector index, GCS,
   Sheets exports — is derived and can be rebuilt from it.
3. **One provider gateway.** Model, TTS, STT, search and email each sit behind an internal
   interface with at least one fallback. No provider SDK is imported outside its adapter.
4. **Boring, single-deployable core.** A modular monolith, not microservices. Split a module
   into a service only when it has a genuinely different scaling or availability profile.
5. **Personal data is a first-class type.** Every field is classified, has a retention rule,
   and is exportable and erasable by user request.

## 2. System context

```
┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│   Browser    │        │  Installed   │        │  Future:     │
│  (Next.js    │        │  PWA         │        │  iOS/Android │
│   web app)   │        │              │        │  (Expo)      │
└──────┬───────┘        └──────┬───────┘        └──────┬───────┘
       │                       │                       │
       └───────────────────────┴───────────────────────┘
                               │  HTTPS · JSON · SSE
                               │  Authorization: Bearer <session JWT>
                    ┌──────────▼───────────┐
                    │   Edge / CDN         │  Cloudflare or GCLB
                    │   TLS, WAF, DDoS,    │
                    │   static asset cache │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   API  (FastAPI)     │  Cloud Run, min-instances ≥ 1
                    │   ─────────────────  │
                    │   auth · chat · mem  │
                    │   files · voice ·    │
                    │   admin · webhooks   │
                    └──┬────┬────┬────┬────┘
                       │    │    │    │
        ┌──────────────┘    │    │    └───────────────┐
        │                   │    │                    │
┌───────▼────────┐ ┌────────▼──┐ │           ┌────────▼─────────┐
│ Postgres 16    │ │  Redis 7  │ │           │ Provider gateway │
│ + pgvector     │ │           │ │           │ ──────────────── │
│ ────────────── │ │ cache     │ │           │ LLM   (Gemini →  │
│ users, threads │ │ rate limit│ │           │        fallback) │
│ messages,      │ │ sessions  │ │           │ TTS   (ElevenLabs│
│ memories(vec)  │ │ locks     │ │           │        → cloud)  │
│ files, audit   │ │ SSE fanout│ │           │ STT, search,     │
└────────────────┘ └───────────┘ │           │ email, geo, fx   │
                                 │           └──────────────────┘
                    ┌────────────▼──────┐
                    │  Workers (ARQ)    │  Cloud Run jobs / service
                    │  ───────────────  │
                    │  doc extraction   │
                    │  memory consolid. │
                    │  embeddings       │
                    │  email, exports,  │
                    │  retention sweeps │
                    └────────┬──────────┘
                             │
                    ┌────────▼──────────┐
                    │  GCS buckets      │  uploads · derived · tts-cache
                    └───────────────────┘
```

## 3. Backend module map

One FastAPI application, one deployable, internally partitioned. Each module owns its tables;
cross-module access goes through the owning module's service functions, never through another
module's tables.

```
app/
├── main.py                  ASGI app, middleware chain, lifespan
├── core/                    config, logging, errors, dependencies, security primitives
├── modules/
│   ├── identity/            Firebase token verification, session issue/refresh, user CRUD
│   ├── profiles/            profile, preferred name, pronunciation, relationships, consent
│   ├── conversations/       threads, messages, streaming orchestration, summarisation
│   ├── memory/              write/extract/consolidate/retrieve, embeddings, quotas
│   ├── knowledge/           uploads, extraction pipeline, chunking, per-user RAG
│   ├── voice/               TTS synthesis + cache, STT, audio artefacts
│   ├── tools/               function-calling tools: fx, timezone, units, geo, web search
│   ├── notifications/       transactional email, push, digests
│   ├── admin/               support console, impersonation with audit, feature flags
│   └── privacy/             export, erasure, consent ledger, retention jobs
├── providers/               one adapter per external service, all behind a Protocol
├── workers/                 ARQ task definitions
└── db/                      SQLAlchemy models, Alembic migrations, seeds
```

### Why a modular monolith

At 19–500 users, microservices buy nothing and cost a distributed-systems tax on every
feature. The module boundary above is the *seam*: `voice` and `knowledge` are the two
plausible extractions later (GPU/CPU-bound, bursty), and both already communicate through the
queue rather than in-process calls, so extracting them is a deployment change, not a rewrite.

## 4. Request flows

### 4.1 Sign-in

```
Browser ──Firebase Google popup / phone OTP──► Firebase Auth
Browser ◄──── Firebase ID token ──────────────
Browser ──POST /v1/auth/session {idToken}────► API
                                               ├─ verify signature against Google JWKs (cached)
                                               ├─ check audience, issuer, expiry, revocation
                                               ├─ upsert user, record login in audit_log
                                               ├─ issue access JWT (15 min) + refresh (30 d, rotating)
                                               └─ set refresh as HttpOnly · Secure · SameSite=Lax cookie
Browser ◄──── {access_token, user, flags} ────
```

Firebase remains the *identity provider* only. The API never trusts a client-asserted email,
profile key, or role again — everything derives from `sub` in the verified token.

### 4.2 A chat turn (the hot path)

```
POST /v1/threads/{id}/messages   {content, attachments[], client_message_id}
  │
  ├─ 1. authn: verify session JWT → user_id
  ├─ 2. authz: thread.user_id == user_id, else 404 (not 403 — do not confirm existence)
  ├─ 3. idempotency: SETNX redis "idem:{client_message_id}" → replay returns the original
  ├─ 4. rate limit: token bucket per user and per IP in Redis
  ├─ 5. persist user message (status=pending) — durable BEFORE any provider call
  ├─ 6. build context, server-side:
  │       system prompt   ← prompt_versions table, pinned per user cohort
  │       profile facts   ← profiles + relationships for THIS user only
  │       memories        ← hybrid retrieval: pgvector ANN + BM25, reranked, top-k
  │       history         ← last N turns verbatim + rolling summary beyond that
  │       attachments     ← chunk refs from knowledge module
  │       budget          ← hard token ceiling; drop lowest-scoring context first
  ├─ 7. call LLM with tool definitions, stream deltas
  ├─ 8. tool loop: fx / timezone / units / geo / web search — each with its own timeout
  ├─ 9. stream to client over SSE, persisting deltas to Redis so a reconnect can resume
  ├─10. on completion: persist assistant message, token counts, cost, latency, model version
  └─11. enqueue async: memory extraction, embedding, title generation, TTS pre-warm
```

Steps 5 and 11 are what make it robust: the turn is durable before the model is called, and
everything non-essential to the reply happens off the request path.

### 4.3 Streaming and reconnection

SSE over `GET /v1/threads/{id}/stream?message_id=…&cursor=…`. Each delta is appended to a Redis
stream keyed by `message_id`; the client sends `Last-Event-ID` on reconnect and the server
replays from the cursor. This survives the mobile-network drops that will otherwise truncate
every long answer. WebSockets are deliberately not used — the traffic is one-directional and
SSE passes proxies and Cloud Run without special handling.

### 4.4 Document upload

```
POST /v1/files/presign → signed GCS PUT URL (content-type and size pinned)
Browser uploads directly to GCS (never through the API — no 100 MB request bodies)
POST /v1/files/{id}/commit → enqueue extraction job
Worker: virus scan → type sniff (not extension) → extract text → chunk → embed → index
        → row in file_chunks, status=ready, SSE notification to the client
```

Extraction moves out of the browser. That fixes three things at once: pdf.js/SheetJS no longer
run on a phone, scanned PDFs can go through OCR, and the extracted text is reusable across
threads instead of re-parsed per message.

### 4.5 Memory lifecycle

The current design — append a line, concatenate all lines into the prompt — degrades as it
grows. Replace with:

```
extract    LLM pass over the completed turn proposes candidate facts with a type and confidence
dedupe     embedding cosine ≥ 0.92 against existing → merge and bump last_confirmed_at
conflict   contradiction of an existing fact → supersede, keep both rows, mark superseded_by
score      salience = f(confidence, recency, times_referenced, user_pinned)
retrieve   query embedding → ANN top-50 → rerank with BM25 + salience → top-8 into the prompt
decay      unreferenced, unpinned, low-confidence facts expire after 180 days
review     the user can see, edit, pin and delete every fact in "My Memory"
```

Sensitive facts (health, bereavement, finances) get `sensitivity='high'`, are never surfaced
proactively, are excluded from any digest or notification, and require an explicit user action
to store.

## 5. Frontend architecture

```
apps/web/
├── app/                     Next.js App Router
│   ├── (marketing)/         public landing, legal pages — static, no PII
│   ├── (auth)/              sign-in, OTP, registration
│   └── (app)/               authenticated shell: sidebar, thread, composer
├── components/              ui/ (shadcn primitives) · chat/ · voice/ · files/
├── features/                one folder per domain: hooks + API client + types
├── lib/                     api client, SSE client, auth, analytics, i18n
└── styles/                  Tailwind config + design tokens (single source of truth)
```

- **Server state** — TanStack Query. Threads, messages, memories, profile.
- **Client state** — Zustand, and only for genuinely ephemeral UI: composer draft, recording
  state, voice mode, panel visibility.
- **Streaming** — a custom hook wrapping `EventSource` with resume-on-reconnect.
- **Design tokens** — the current `:root` variables become Tailwind theme tokens. Light/dark
  becomes a `data-theme` attribute. Every `!important` override block is deleted, not ported.
- **Rendering model output** — markdown → AST → React nodes. Never `innerHTML`. Model output is
  untrusted input; this is the single most important frontend change.
- **PWA** — a real service worker (Serwist): precache the shell, network-first for API,
  stale-while-revalidate for assets, offline fallback page, background sync for queued messages.
- **Accessibility** — Radix primitives underneath shadcn give focus traps, roles and keyboard
  handling for free. Target WCAG 2.2 AA.

## 6. Cross-cutting concerns

| Concern | Where it lives |
|---|---|
| Authentication | ASGI middleware → `request.state.user` |
| Authorisation | Per-resource dependency; every query filtered by `user_id`; Postgres RLS as the backstop |
| Rate limiting | Redis token bucket, per user + per IP + per endpoint class |
| Idempotency | `client_message_id` unique index + Redis short-term key |
| Validation | Pydantic v2 models at every boundary, in and out |
| Errors | One envelope: `{error: {code, message, request_id, details?}}` |
| Tracing | OpenTelemetry, `trace_id` propagated to the client in responses |
| Config | Pydantic Settings; secrets from Secret Manager, never env files in the image |
| Feature flags | DB-backed, per user and per cohort; the LLM prompt version is one of them |
| Migrations | Alembic, forward-only, applied by a pre-deploy job |

## 7. What explicitly does *not* change

- Firebase Auth for Google sign-in and phone OTP.
- ElevenLabs for the voice, with a cloud-TTS fallback for cost control on long replies.
- The persona and its tone rules — moved into a versioned `prompt_versions` table.
- "Push to deploy" as an operating requirement.
- The four supported languages.

# 09 — Migration Roadmap

Nine phases. Each has a goal, the work, and an exit criterion that is checkable rather than
felt. Durations assume roughly one focused developer; treat them as sequencing, not commitments.

**The strangler pattern applies throughout.** The existing `index.html` keeps serving real users
from `main` while the new stack is built beside it. Users move once, at Phase 6, behind a flag.

---

## Phase 0 — Stop the bleeding (days, not weeks) 🔴

Independent of the rewrite. Ship on `main`.

- Remove `PROFILES`, `BACKGROUNDS`, `FOLLOWUP` and `USER_CTX` from the bundle; serve them from
  an authenticated backend endpoint keyed on the verified Firebase UID.
- Require a Firebase ID token on every backend endpoint. Derive `profileKey` server-side.
  Reject any client-supplied identity.
- Move system-prompt assembly to the backend; stop accepting a client-built `contents` array.
- Pin `/send-email` to server-side templates and recipients.
- Add a CSP header and SRI hashes to the four CDN scripts.
- Stop rendering model output through `innerHTML`; escape thread previews and filenames.
- Fix `switchThread()` — the `#topbar-thread` / `#topbar-sub` references throw on every call
  (finding H1), which is why new conversations produce no greeting.
- Stop wiping `b_threads` on `pagehide` until server-side history exists (finding H2).
- **Decide and act on the git history.** The personal data is in 174 public commits. Either
  make the repository private, or rewrite history and force-push. Then inform the 19 people —
  several of these details are medical.

**Exit:** an unauthenticated `curl` to any backend endpoint returns 401; View Source contains
no personal data; no `innerHTML` path renders model output.

---

## Phase 1 — Foundations (2–3 weeks)

- Monorepo scaffolding: `apps/web`, `apps/api`, `apps/workers`, `packages/*`, `infra/*`.
- `docker compose` local stack with mock providers; `make bootstrap`.
- Terraform for staging: VPC, Cloud SQL, Redis, GCS, Cloud Run, Secret Manager.
- CI skeleton: lint, type-check, test, build, deploy to staging.
- FastAPI app with health checks, error envelope, request IDs, structured logging, OTel.
- Alembic + the full schema from [04](04-data-model.md).
- Sentry, dashboards, alerting.

**Exit:** a hello-world endpoint deploys to staging through CI, with a trace visible end to end.

---

## Phase 2 — Identity & profile (2 weeks)

- Firebase ID token verification, session issue/refresh with rotation, revocation.
- `/me`, preferences, relationships, quotas, consents.
- Role model and the audit log.
- RLS policies and the cross-tenant security test suite.
- Migrate `Users` from Sheets → `users` + `profiles`.

**Exit:** the security suite proves user A cannot reach user B's data by any route; every
family member exists as a row with a linked Firebase UID.

---

## Phase 3 — Conversations & streaming (3 weeks)

- Threads and messages, server-side, paginated, durable.
- Server-side prompt assembly from `prompt_versions`.
- The provider gateway with the incumbent model plus one fallback.
- SSE streaming with Redis-backed resume.
- Token budgeting, rolling summarisation, tool-calling framework.
- Postgres FTS search over threads and messages.

**Exit:** a full conversation survives a page reload, a network drop mid-stream, and a server
restart. p95 first token < 2 s in staging.

---

## Phase 4 — Memory (2 weeks)

- Extraction, deduplication, conflict resolution, salience scoring, decay.
- Embeddings + hybrid retrieval (pgvector ANN + BM25 + rerank).
- The "My Memory" surface: view, edit, pin, delete, provenance.
- Server-side quotas with an accurate meter.
- Migrate Sheets memories with embeddings backfilled; import the hardcoded biographies
  **with consent**, not silently.

**Exit:** retrieval precision measured against a labelled set; a user can see and correct every
fact Bella holds; the meter matches the database.

---

## Phase 5 — Files, voice, tools (3 weeks)

- Presigned uploads, malware scan, server-side extraction (PDF/DOCX/XLSX/PPTX + OCR), chunking,
  embedding, per-user RAG.
- TTS with an aggressive cache; STT fallback for non-Chrome browsers.
- Tools: fx, timezone, units, geocode, web search, GIFs — all server-side, all cached, all
  invoked by the model rather than by keyword sniffing.
- Recording upload and transcription.

**Exit:** a 200-page scanned PDF is answerable; TTS cache hit ratio > 60% in staging; voice
input works in Safari.

---

## Phase 6 — The new frontend & cutover (4 weeks)

- Next.js app: auth flows, chat shell, composer, streaming, sidebar, memory, files, settings,
  converter, account, legal pages.
- Design tokens ported from the current `:root`; the 15 override blocks are deleted, not carried.
- Markdown → sanitised AST → React.
- Real PWA with Serwist; offline shell and background sync.
- i18n scaffolding for en/hi/bn/mr.
- WCAG 2.2 AA pass.
- Playwright e2e and the LLM eval suite green.

**Cutover.** Behind a flag: owner first, then one family, then everyone. The old
`index.html` stays deployed at a legacy path for two weeks with a banner. Roll back = flip the
flag.

**Exit:** every family member has completed a session on the new app; the eval suite shows no
persona regression; zero P1 bugs for 7 days.

---

## Phase 7 — Hardening & compliance (2 weeks)

- Penetration test (external if budget allows, otherwise a structured internal review against
  the threat model in [06](06-security-privacy.md)).
- Load test at 10× expected peak.
- DR drill: restore from PITR into a scratch project, measure RTO.
- Real Terms, Privacy Policy, Security page, and a DPIA.
- Export and erasure flows working end to end, with their SLA clocks.
- Runbooks written and rehearsed.
- Freeze the old repository as an archive.

**Exit:** no critical or high findings open; restore completes within RTO; a deletion request
executed end to end and verified in the database, GCS and the search index.

---

## Phase 8 — Bella 2.0 features

Only now do the roadmap items in the sidebar's "Coming Soon" grid become cheap, because the
platform exists:

Gmail · Drive · Calendar · Meet (OAuth scopes, per-scope consent, tool definitions) ·
Maps & navigation · Charts (structured output → Recharts) · File generation and download ·
Document conversion · Image transformation · Proactive notifications (the `followup` memories
already model this) · Voice ID (speaker verification — high privacy sensitivity, needs explicit
consent) · Smart home.

Each is a tool implementation plus a consent record plus an eval case. None requires
architectural change.

---

## Sequencing notes

**What must be serial.** 1 → 2 → 3 is a hard chain: no conversations without identity, no
identity without infrastructure. Phase 0 is parallel to all of it and comes first in wall-clock
time.

**What can run in parallel.** Phase 4 (memory) and Phase 5 (files/voice) are independent once
Phase 3 lands. Frontend work in Phase 6 can start against the API contract as soon as Phase 3's
OpenAPI schema is stable — the generated client makes that safe.

**Total:** roughly 18–20 weeks of focused work to the Phase 7 exit.

**The riskiest item is not technical.** It is Phase 6's cutover: 19 non-technical users, some
of them children, some elderly, all of whom have a relationship with the current app. Budget
real time for hand-holding, keep the old version one click away, and do not change the persona
and the platform in the same week.

## What to cut if time is short

In order of what hurts least:
1. Phase 8 entirely — the current app does not have these features either.
2. STT fallback and OCR (Phase 5) — degrade gracefully instead.
3. i18n scaffolding — keep the four languages working through the model, defer UI translation.
4. The visual regression suite.

**Do not cut:** Phase 0, the security test suite in Phase 2, RLS, streaming resume, or the LLM
eval suite. Those are what "robust" means here.

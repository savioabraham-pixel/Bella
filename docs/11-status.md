# 11 — Status & Gap Analysis

Where the rewrite actually stands against [09](09-migration-roadmap.md), what remains of
`index.html` to replicate, and what to do next.

**As of:** 2026-08-12 · branch `beta` at `438f473` · `main` at `a8009a2`

---

## Phase scorecard

| Phase | Status | Evidence |
|---|---|---|
| 0 — Stop the bleeding | 🔴 **Not started** | `PROFILES`/`BACKGROUNDS`/`FOLLOWUP`/`USER_CTX` still literal at `index.html:1451–1521` on `main`; `profileKey` appears 27× |
| 1 — Foundations | 🟡 **~80%** | Monorepo, compose stack, CI, FastAPI app, full schema, OTel all real. No Terraform, no staging deploy, no Sentry |
| 2 — Identity & profile | 🟢 **Substantially done** | Auth and `/me` shipped and green (79 tests). Outstanding: `POST /me/avatar` (needs Phase 5 storage), migrating the 19 legacy users |
| 3 — Conversations | 🟠 **In progress** | Threads, messages, pagination, idempotency and FTS search shipped. Generation, streaming, prompt assembly and the provider gateway outstanding |
| 4 — Memory | ⚪ Not started | Tables exist; no endpoints |
| 5 — Files, voice, tools | ⚪ Not started | Tables exist; no endpoints |
| 6 — Frontend & cutover | 🟠 **Started** | Working chat UI on port 3000: sign-in, threads, streaming, Stop, Try again. Firebase auth, PWA, i18n, a11y pass and cutover outstanding |
| 7 — Hardening | ⚪ Not started | — |
| 8 — Bella 2.0 | ⚪ Not started | — |

## What Phase 1 actually delivered

Better than "scaffolding" suggests, and worth knowing about before building on it:

- **App factory** (`app/main.py`) — lifespan-managed engine and Redis, ordered middleware with
  request context outermost, security headers, gzip, conditional OTel, OpenAPI and docs
  suppressed in production.
- **Error envelope** (`app/core/errors.py`) — one shape for every failure, handlers for
  `AppError`, validation, `IntegrityError` and `SQLAlchemyError`, and a documented rule that a
  resource belonging to someone else is a 404 rather than a 403.
- **Redaction pipeline** (`app/core/logging.py`) — sensitive keys dropped and free text scrubbed
  by a structlog processor, so no call site can leak message content, a token or a phone number
  by forgetting to.
- **RLS that actually fires** (`migrations/…_row_level_security.py`) — the runtime role is not
  the table owner, `app.user_id` is transaction-local, and 13 owned tables plus one derived
  join-table policy are covered. `RLS_TABLES` in `db/models/__init__.py` is asserted against the
  live database by the security suite.
- **Schema ahead of the API** — `Message`, `MessageAudio`, `Memory`, `File`, `FileChunk`,
  `PromptVersion`, `ToolInvocation`, `Consent`, `DataRequest`, `AuditLog` all exist already.

**The consequence worth planning around:** no phase from here is blocked on data modelling.
Phases 3–7 are "write endpoints against tables that already exist," which is a materially
cheaper shape of work than the roadmap's week estimates assume.

### What Phase 1 still owes

1. **Terraform for staging.** `infra/` contains Dockerfiles only. The Phase 1 exit criterion —
   a hello-world endpoint deployed to staging through CI with a trace visible end to end — is
   not met.
2. **A deploy job.** CI runs `api-quality`, `web-quality`, `api-tests`, `security` and
   `prod-images`. `prod-images` proves an image *starts*; nothing ships.
3. **Sentry.** `sentry_dsn` is declared in settings and read by nothing.

---

## What remains to be replicated

`main:index.html` is 5,540 lines and roughly 190 functions. By destination:

| Domain | ~fns | Representative | Lands in |
|---|---|---|---|
| Identity & registration | 15 | `submitRegistration`, `verifyOTP`, `notYouGate`, `resolveByLastName`, `confirmIdentity` | Phase 2 |
| Conversation | 25 | `callBella`, `buildSystemPrompt`, `switchThread`, `summariseThread`, `getHist`, `generateSuggestions`, `retryMsg`, `editMsg` | Phase 3 |
| Memory | 6 | `loadUserMemory`, `detectMemoryRequest`, `detectPronunciationCorrection`, `updateMemoryProgress` | Phase 4 |
| Documents | 5 | `extractPDF`, `extractDOCX`, `extractExcel`, `extractPPTX` | Phase 5 (server-side) |
| Voice | 10 | `startMic`, `toggleVoiceMode`, `_callBackendSpeak`, `openVideoRecording` | Phase 5 |
| Tools / converter | 30 | `doConvert`, `doTZConvert`, `doUnitConvert`, `fetchExchangeRates`, `initLocation`, `insertGif` | Phase 5 |
| UI shell | 100 | sidebar, drag/resize, emoji picker, photo cropper, clock, themes, `_mdBuildTable` | Phase 6 |

**Over half the function count is UI plumbing.** Next.js plus the already-installed
`react-markdown`, `rehype-sanitize`, `zustand` and `react-hook-form` delete most of it rather
than port it — the hand-rolled markdown table builder (`_mdBuildTable`, `_mdCells`, `_mdInline`,
`_mdExtractTables`) and the drag/resize handlers in particular. Do not read 5,540 lines as
5,540 lines of rewrite.

### Client state to migrate

Sixteen `localStorage` keys become server state: `b_threads`, `b_registered_user`,
`b_returning_*` (5 keys), `b_userid`, `b_active_session`, `b_location_data`, `b_location_perm`,
`b_dark`, `b_clock_fmt`, `b_pref_*`, `b_gmail`, `b_thought_cache`.

`b_threads` is currently wiped on `pagehide` (finding H2) — history loss is a live bug on
`main`, not only a migration concern.

### External dependencies to absorb

Four CDN scripts (`pdf.js`, `mammoth`, `SheetJS`, `JSZip`) move server-side in Phase 5.
`open.er-api.com` and `nominatim.openstreetmap.org` are called directly from the browser today
and become `/tools/fx` and `/tools/geocode`, cached, with a compliant User-Agent.

---

## Next steps

### 1. Phase 0 on `main` — this week 🔴

Unchanged in urgency since [09](09-migration-roadmap.md) was written, and `beta` does nothing
about it for months. Nineteen people's biographical details, several of them medical, are in a
publicly served file and in 174 public commits.

Make the repository private first — it is one click and it buys time for the rest. Then strip
the four constants, require a Firebase ID token on the Cloud Run endpoints, and fix
`switchThread()`, whose `#topbar-thread` reference throws on every call and is why new
conversations produce no greeting.

### 2. Close the Phase 1 tail

Terraform for staging and a CI deploy job. Phase 2 needs somewhere to run, and a security
suite that has never executed against a deployed environment has not been tested.

### 3. Phase 2 — identity ✅ done

`/auth/session`, `/refresh`, `/logout`, `/logout-all`, `GET /sessions`,
`DELETE /sessions/{id}`, and the full `/me` surface — profile, preferences, relationships,
quotas, consent ledger. 79 tests green, mypy clean.

Identity is verified directly against Google's signing certificates rather than through
`firebase-admin`; sessions are refresh-token families with rotation and reuse detection;
`CurrentUser` is the only route-level source of a subject, so there is no parameter a client
could supply to name someone else.

Still outstanding in this phase:

- `POST /me/avatar` — needs presigned object storage, so it waits for Phase 5.
- **Migrating the 19 existing users.** They must arrive as rows keyed on their Firebase UID.
  Accounts are deliberately never linked by email, so this is an explicit import, not
  something that happens on first sign-in.

### 4. Publish the OpenAPI schema ✅ done

`packages/api-client/openapi.json` now describes 14 paths and 24 schemas. **Phase 6 frontend
work can start in parallel from here** — that is the single biggest lever on the 18–20 week
estimate, because Phase 6 is the longest phase and is otherwise serial after Phase 3.

Regenerate with `make openapi` whenever the contract changes.

### 5. Phase 3 — conversations and streaming 🟠 in progress

**Done.** `GET/POST /threads`, `GET/PATCH/DELETE /threads/{id}`,
`GET/POST /threads/{id}/messages`, `GET /search`. Keyset pagination throughout, thread-row
locking so concurrent sends cannot collide on `seq`, `client_message_id` idempotency so a
double-tap on Send does not bill twice, and Postgres full-text search across all of a
person's conversations rather than the browser substring scan over whatever happened to be
loaded.

The property the phase exists for is in place: **a turn is durable before the model is
called.** The user message and a `pending` assistant row commit together, so a crash
mid-generation leaves a retryable record instead of nothing (finding H2).

**Also done — Bella now answers.** The provider gateway is in: `LLMProvider` with an
in-process mock (the default, so development and CI need no vendor account and no request is
ever billed) and a Gemini adapter for the incumbent, plus a narrow failover that tries a
second vendor only for failures a second vendor could plausibly survive.

**`buildSystemPrompt()` is dead.** The persona lives in `prompt_versions` as versioned,
rollback-able data seeded by migration; the personal detail is rendered at request time from
the caller's own `profiles` and `relationships` rows under RLS. The client contributes
exactly one thing to the prompt: the text of the message it just sent. That closes the last
open piece of finding C2.

**Also done — replies stream.** `GET /threads/{id}/stream` is Server-Sent Events: `delta`
frames, then one `done` or `error`, with heartbeat comments so proxies leave the connection
open. Deltas are mirrored into a Redis stream keyed on the assistant message, which buys
three things at once:

- **Reconnect resumes.** `Last-Event-ID` replays from the entry after it. The reply is
  generated, and paid for, once.
- **Generation outlives the request.** It runs detached with its own session, so a tunnel
  dropping at word thirty does not discard the answer.
- **Two tabs cost one reply.** A Redis `SET NX` lock decides who generates; the other tails.

A reply whose Redis window has expired is still served — synthesised back from Postgres,
which is the durable copy.

**Also done — the prompt fits a budget, and long conversations compress.** History is
assembled to a token budget rather than a turn count, and turns that no longer fit are
compressed into `threads.summary` with `summary_upto_seq` as the watermark. Summarisation
runs *after* a reply is delivered and triggers at 70% of budget, so the turn that crosses
the line is not the turn that waits for the summary. Turns at or below the watermark are
never also sent verbatim.

`bella.summary` is a versioned prompt row like the persona, and `bella.system` moved to
version 2 to carry a CONVERSATION SO FAR section — version 1 deactivated rather than
edited, which is what versioned prompts are for.

**Also done — orphaned generations are swept.** A reply interrupted by a process restart
used to leave its row on `streaming` with nothing to finish it: a spinner that never
resolved. A startup pass now moves rows older than the grace period to `failed` with a
retryable error, through a SECURITY DEFINER function since the sweep crosses users.

**Next, in order.**

1. `POST /threads/{id}/messages/{mid}/cancel` and `/regenerate`, and
   `POST /threads/{id}/summarise`. `cancelled` is already a valid message status.
2. **A real auto-titler.** Titles are derived from the opening question, not generated.
3. **Tool calling.** `tool_call` / `tool_result` are in the event contract and not yet
   emitted; `tool_invocations` is modelled and unused.
4. Rate limiting. `rate_limit_enabled` and the per-class limits in [05](05-api-contract.md)
   are configured and unenforced.

**Known limitation.** Token counts are estimated from character classes, not tokenised. The
estimate feeds budget decisions only — billing and usage come from the vendor's
`usageMetadata` on the message row — but a badly wrong estimate would move the compression
boundary. The divisor is deliberately pessimistic for Indic scripts, where a ratio tuned for
English would under-count and overflow the real window.

**Known limitation.** Search uses the `simple` text-search configuration, matching the
generated `content_tsv` column. That is deliberate — the family speaks four languages and an
English stemmer would mangle Hindi, Bengali and Marathi — but it means no stemming, so
"running" does not match "run". Thread-title search has no index behind it; at 500 threads
per user that is fine, and it wants a trigram index before it is not.

---

## A proposed change to the roadmap

**Pull the converter tools forward from Phase 5 into a Phase 2.5.**

`/tools/fx`, `/tools/timezone`, `/tools/units` and `/tools/geocode` are self-contained: no LLM,
no streaming, no file storage, no new tables beyond `tool_invocations`, which exists. They are
the cheapest possible real endpoint surface on which to validate bearer auth, rate limiting,
the cache layer, idempotency and the error envelope end to end — before the streaming
architecture in Phase 3 is built on top of those same primitives.

They also correspond to ~30 functions of `index.html` and a whole UI panel, so the work is not
speculative. The cost is roughly a week; the benefit is discovering that the auth or
rate-limit design is wrong while the blast radius is four GET endpoints.

---

## Open decisions

| # | Decision | Owner | Blocking |
|---|---|---|---|
| 1 | Private repository, or rewrite history and force-push? | Savio | Phase 0 |
| 2 | Notify the 19 people about the exposure — when and how? | Savio | Phase 0 |
| 3 | GCP project and billing account for staging | Savio | Phase 1 tail |
| 4 | Which LLM provider is the incumbent, and which is the fallback? | Savio | Phase 3 |
| 5 | Adopt the Phase 2.5 proposal above? | Savio | Phase 3 sequencing |

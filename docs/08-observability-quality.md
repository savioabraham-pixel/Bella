# 08 — Observability & Quality

The current app has neither. Errors are swallowed by ~30 bare `catch{}` blocks, telemetry is
`console.warn`, and there is no test of any kind. Both gaps are load-bearing for "robust".

---

## 1. Observability

### Structured logging

JSON to stdout, collected by Cloud Logging. Every line carries `request_id`, `trace_id`,
`user_id` (hashed in any log that leaves the project), `route`, `duration_ms`, `status`.

**Never logged:** message content, memory content, extracted document text, tokens, phone
numbers, email addresses, precise coordinates. A redaction filter runs on the logger, not at
each call site, so forgetting is not possible.

### Tracing

OpenTelemetry, auto-instrumented for FastAPI, SQLAlchemy, Redis and httpx, with manual spans
around the parts that actually matter:

```
POST /threads/{id}/messages          2 380 ms
├── authz                                 3 ms
├── build_context                       210 ms
│   ├── retrieve_memories                140 ms   (pgvector ANN + rerank)
│   ├── load_history                      45 ms
│   └── render_prompt                     25 ms
├── llm.generate                      1 890 ms   (model, tokens in/out, finish_reason)
│   └── tool.web_search                 640 ms
├── persist_message                      35 ms
└── enqueue_followups                    12 ms
```

`trace_id` is returned in the response header and shown in the UI's error state, so a family
member reporting "it broke" gives you a lookup key.

### Metrics (RED + domain)

| Metric | Alert |
|---|---|
| `http_requests_total{route,status}` | 5xx rate > 2% for 5 min |
| `http_request_duration_seconds` | p95 > 3 s for 10 min |
| `llm_first_token_seconds` | p95 > 3 s |
| `llm_tokens_total{model,direction}` | daily cost > budget × 0.8 |
| `llm_errors_total{provider,type}` | > 5 consecutive → circuit opens |
| `tts_cache_hit_ratio` | < 0.5 (cost regression) |
| `memory_retrieval_precision` | tracked weekly against a labelled set |
| `queue_depth{queue}` | > 100 for 5 min |
| `db_connections_active` | > 80% of pool |
| `sse_disconnects_total` | spike ⇒ proxy or timeout regression |

### Error tracking

Sentry on both sides, releases tied to the commit SHA, source maps uploaded. PII scrubbing on.
Frontend errors carry a breadcrumb trail but never message content.

### Product analytics

PostHog (self-hosted or EU cloud) or plain SQL over a read replica. Events are declared in a
typed registry — no ad-hoc string names. Track activation (first conversation), retention
(weekly returning users), feature use (voice vs. text, documents, converter), and the memory
loop (facts stored, edited, deleted). Analytics is consent-gated.

### Dashboards

1. **Health** — request rate, error rate, latency, dependency status.
2. **Cost** — tokens and dollars by user, model and day; TTS characters; cache hit ratio.
3. **Quality** — thumbs up/down rate, regeneration rate, memory corrections per user, tool
   failure rate.
4. **Business** — DAU/WAU, conversations per user, message length distribution.

---

## 2. Test strategy

Target coverage: **80% on the backend, 70% on the frontend**, with the caveat that coverage
percentage is a floor, not a goal.

### Backend

| Layer | Tool | Scope |
|---|---|---|
| Unit | pytest | Pure logic: context assembly, token budgeting, memory scoring, quota maths, prompt rendering |
| Integration | pytest + testcontainers | Real Postgres + Redis: repositories, RLS policies, migrations up *and* down, transaction behaviour |
| Contract | schemathesis | Fuzz every endpoint against the OpenAPI schema |
| Provider | `respx` | Recorded fixtures for each provider, including timeout, 429 and malformed-response paths |
| Security | pytest | A dedicated suite that asserts user A cannot read user B's threads, messages, memories or files — via API *and* via a repository call with a missing filter |
| Load | k6 | 50 concurrent streams; assert p95 and no connection exhaustion |

### Frontend

| Layer | Tool | Scope |
|---|---|---|
| Unit | Vitest | Hooks, formatters, the SSE client's reconnect logic |
| Component | Testing Library | Composer, message list, memory editor, file chips |
| E2E | Playwright | Sign-in → send → stream → reload-persistence; upload → answer; voice mode; memory edit; delete account |
| Visual | Playwright screenshots | Light and dark, mobile and desktop |
| A11y | axe-core | Zero critical violations on the key screens |

### LLM quality — the part most teams skip

A rewrite that ships a technically perfect API and a Bella who feels wrong is a failed
migration. Treat the persona as testable:

**Golden set.** 100–200 recorded (input, context, expected-behaviour) cases drawn from real
usage, covering: correct preferred name, the mandatory family follow-up after a positive
response, empathy-first on a negative response, the Sreya/Purbita disambiguation, never
mentioning Shayani's bereavement unprompted, refusing to reveal one user's data to another,
tone limits (≤1 endearment, no flowery metaphor), and Hinglish/Benglish handling.

**Assertions** are a mix of deterministic checks (does the reply contain the exact family
names? does it avoid a forbidden token?) and an LLM-as-judge rubric for tone, scored against a
threshold.

**Run it** on every prompt-version change and every model change, in CI, blocking promotion to
production. Record scores per version so a regression is visible as a number, not a complaint.

**Safety suite.** Prompt-injection attempts embedded in uploaded documents and web results;
cross-user data requests; jailbreak attempts; PII leakage probes. These are pass/fail.

**Online signals.** Thumbs up/down per message, regeneration rate, memory correction rate, and
conversation abandonment — reviewed weekly alongside the offline scores.

---

## 3. Definition of done

A change is done when: tests pass at the required coverage; the OpenAPI schema and generated
client are regenerated; migrations run forward and backward cleanly; the security suite still
passes; the eval suite has not regressed; dashboards and alerts cover any new failure mode;
docs and changelog are updated; and it has run in staging for 24 hours.

## 4. Quality gates in CI

```
lint  →  type-check  →  unit  →  integration  →  security-suite  →  build
      →  preview deploy  →  e2e  →  a11y  →  llm-eval  →  merge allowed
```

Nothing merges red. There is no override; if a gate is wrong, fix the gate.

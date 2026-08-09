# Bella — Architecture & Productionisation Docs

These documents describe **where Bella is today** (one 5,540-line `index.html`) and **what a
robust, full-fledged application looks like**, with the stack, data model, contracts,
security posture, infrastructure and a staged migration plan to get there.

Written on branch `beta`. Nothing here changes `index.html` or `main`.

## Read in this order

| # | Document | What it answers |
|---|---|---|
| 01 | [Current state analysis](01-current-state.md) | What is in the repo, how the file is built, and every defect/risk found |
| 02 | [Target architecture](02-target-architecture.md) | The shape of the system — services, boundaries, request flows |
| 03 | [Technology stack](03-tech-stack.md) | Which DB, framework, queue, storage, and **why**, with alternatives rejected |
| 04 | [Data model](04-data-model.md) | Postgres schema, pgvector memory design, retention |
| 05 | [API contract](05-api-contract.md) | Every endpoint, auth, streaming, error envelope |
| 06 | [Security & privacy](06-security-privacy.md) | Threat model, authz, PII, DPDP/GDPR obligations |
| 07 | [Infrastructure & DevOps](07-infra-devops.md) | Environments, IaC, CI/CD, cost |
| 08 | [Observability & quality](08-observability-quality.md) | Logging, tracing, evals, test strategy |
| 09 | [Migration roadmap](09-migration-roadmap.md) | Phased plan, sequencing, exit criteria per phase |
| 10 | [Decision log (ADRs)](10-decision-log.md) | The 14 decisions that define the architecture, each with rationale |

## The 60-second version

**Where we are.** A single static `index.html` on GitHub Pages, talking to one private Cloud
Run backend. Google Sheets is the database. Nineteen real people's biographical details —
including bereavements and medical history — are hardcoded as JavaScript constants in a
publicly served file. Backend endpoints accept an unauthenticated `profileKey` and return that
person's memories. There is no build, no test, no CI, no dependency manifest, and no
service worker despite the PWA claim.

**Where we should go.**

```
Next.js 15 PWA  →  FastAPI (Python 3.12)  →  Postgres 16 + pgvector   (system of record)
                                          →  Redis                    (cache, rate limit, streams)
                                          →  GCS                      (files, audio, recordings)
                                          →  Provider gateway         (LLM / TTS / STT / search)
```

Identity stays on **Firebase Auth** (Google + phone OTP already work); the backend verifies the
Firebase ID token and issues its own short-lived session. Every profile, background,
preference and memory moves out of the bundle and into Postgres behind row-level
authorisation. Chat responses stream over SSE. Long-term memory becomes a retrieved,
scored, expiring store rather than a blob concatenated into the system prompt.

**How we get there.** Nine phases in [09](09-migration-roadmap.md). Phase 0 (stop the
bleeding: pull PII out of the bundle, authenticate the backend) is urgent and independent of
everything else — it should ship before any rewrite work starts.

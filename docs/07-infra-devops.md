# 07 — Infrastructure & DevOps

## 1. Environments

| Env | Purpose | Data | Deploy |
|---|---|---|---|
| `local` | Development | Docker Compose, seeded synthetic data | `docker compose up` |
| `preview` | One per pull request | Ephemeral DB from a template, synthetic data | Auto on PR open, torn down on merge |
| `staging` | Pre-production, identical shape | Anonymised subset — **never a production dump** | Auto on merge to `main` |
| `production` | Live | Real | Manual approval, then automated |

Branching: trunk-based. `main` is always deployable. Short-lived feature branches, squash
merge, conventional commits. Releases are tagged and changelogs generated — a direct answer to
the 170 identical `Update index.html` commits.

## 2. Local development

```yaml
# docker-compose.yml (shape)
services:
  postgres:    { image: pgvector/pgvector:pg16, ports: ["5432:5432"] }
  redis:       { image: redis:7-alpine,          ports: ["6379:6379"] }
  api:         { build: apps/api,     command: uvicorn --reload, depends_on: [postgres, redis] }
  worker:      { build: apps/api,     command: arq workers.main.WorkerSettings }
  web:         { build: apps/web,     command: pnpm dev }
  mock-providers: { build: tools/mock-providers }   # deterministic LLM/TTS/search stubs
  mailpit:     { image: axllent/mailpit }           # catches outbound mail
```

`make bootstrap` → dependencies, `.env` from `.env.example`, migrations, seed data, a demo
user. A new contributor should be running the full stack in under ten minutes with no cloud
credentials — the mock provider server makes that possible.

## 3. GCP topology

```
project: bella-prod                        project: bella-staging (identical, smaller)
├── VPC (private)
│   ├── Cloud Run: bella-api        min=1  max=20  concurrency=60  cpu=1  mem=1Gi
│   ├── Cloud Run: bella-worker     min=0  max=10  cpu=2  mem=2Gi  cpu-always-allocated
│   ├── Cloud Run: bella-web        min=1  max=10
│   ├── Cloud SQL: postgres-16      HA regional, private IP, PITR 35d, automated backups
│   ├── Memorystore Redis           1 GB standard HA
│   └── Serverless VPC connector
├── GCS: bella-uploads / bella-derived / bella-tts-cache / bella-exports / bella-backups
├── Secret Manager: provider keys, JWT signing key, DB credentials
├── Artifact Registry: container images, vulnerability scanning on push
├── Cloud Scheduler → Cloud Run jobs: retention sweep, memory consolidation,
│                                     Sheets export, usage rollup, backup verification
└── Cloud Monitoring / Logging / Trace
```

Region **`asia-south1` (Mumbai)** — matches the existing backend and the majority of users, and
keeps Indian personal data in-country, which simplifies DPDP posture. Cloudflare in front gives
US and UK users edge termination.

## 4. Infrastructure as Code

Terraform, one module per concern (`network`, `database`, `cache`, `storage`, `run-service`,
`monitoring`, `iam`), composed per environment. State in a GCS backend with locking. No console
clicks — if it exists in production and not in `infra/terraform`, it is a defect.

Each service gets its **own service account** with only the roles it needs. The API can read
secrets and write to its buckets; it cannot deploy, cannot read other projects, and cannot
delete backups.

## 5. CI/CD

```
PR opened
├─ lint          ruff, mypy --strict, eslint, prettier, tsc --noEmit
├─ test          pytest (unit + integration on testcontainers) · vitest · coverage gate
├─ security      gitleaks, pip-audit, npm audit, Trivy on the built image, Semgrep
├─ build         API + web images → Artifact Registry, tagged with the commit SHA
├─ preview       ephemeral env, migrations applied, seeded
├─ e2e           Playwright against the preview URL
└─ a11y          axe-core on the key screens

merge to main
├─ deploy staging (automatic)
├─ smoke tests + a synthetic chat turn
├─ LLM eval suite vs. the golden set (see 08)
└─ deploy production
   ├─ migrations as a pre-deploy Cloud Run job (forward-only, backwards-compatible)
   ├─ canary 10% for 10 minutes, watching error rate and p95
   ├─ promote to 100%
   └─ auto-rollback on SLO breach
```

**Migration discipline.** Expand → migrate → contract. Add the column, backfill, dual-write,
switch reads, then drop the old column in a later release. Never a destructive migration in the
same deploy as the code that needs it.

**Secrets.** Only in Secret Manager, injected as env vars at runtime. No `.env` in an image,
no secret in CI logs. Rotation: provider keys quarterly, JWT signing key monthly with an
overlap window.

## 6. Reliability

| | Target |
|---|---|
| API availability | 99.5% monthly |
| Chat first-token p95 | < 2.0 s |
| Chat completion p95 | < 12 s |
| Page load (LCP) p75 | < 2.5 s |
| Error budget | 0.5% ≈ 3.6 h/month |

**Failure handling.**

- Provider timeouts: 30 s for chat, 10 s for TTS, 5 s for tools.
- Circuit breaker per provider — open after 5 consecutive failures, half-open after 30 s.
- Retries with jittered exponential backoff on idempotent calls only.
- Graceful degradation: TTS unavailable → text-only reply with a notice; search unavailable →
  answer from the model with a caveat; primary model unavailable → fallback model, and the
  reply is labelled.
- Backpressure: queue depth thresholds shed non-critical work (embeddings, title generation)
  before user-facing work.

**Backups.** Cloud SQL automated backups plus 35-day PITR. Weekly logical dump to a separate
bucket with a distinct retention lock. **Restore is tested monthly** into a scratch project —
an untested backup is not a backup. GCS buckets have versioning and a lifecycle policy.

**DR.** RPO 1 hour, RTO 4 hours. Documented runbook: restore DB from PITR, redeploy the last
known-good image tag, replay the queue, verify with the smoke suite.

## 7. Cost model

Order-of-magnitude, monthly, at ~50 daily active users:

| Item | Estimate |
|---|---|
| Cloud Run (api min=1, web min=1, workers) | $40–70 |
| Cloud SQL (db-g1-small HA) | $70–100 |
| Memorystore Redis 1 GB | $35 |
| GCS + egress | $5–15 |
| LLM tokens | $30–150 — the dominant variable; depends entirely on context discipline |
| ElevenLabs | $22–99 — **the cache is the lever**; identical replies must not re-synthesise |
| Cloudflare, Sentry, monitoring | $0–50 |
| **Total** | **~$200–500/month** |

Compared with today's near-zero (Pages + one scale-to-zero Cloud Run + free Sheets), this is
the actual price of durability, authorisation and support. Reductions if that matters: drop
Cloud SQL HA in favour of a single zone with PITR (−$40), use Upstash serverless Redis (−$30),
run min-instances 0 and accept cold starts (−$25), and be ruthless about prompt size.

Cost controls: per-user token ceilings, a budget alert at 80%, a hard cap that degrades to a
cheaper model rather than failing, and a daily cost-per-user dashboard.

## 8. Operations

- **On-call** is one person; the alert policy must respect that. Page only on: API down,
  error rate > 5% for 5 minutes, DB unreachable, or budget exceeded. Everything else is a
  daily digest.
- **Runbooks** in `docs/runbooks/`: provider outage, DB failover, bad deploy rollback, data
  request handling, security incident.
- **Status page** for the family — they will otherwise text the owner.
- **Change log** in-app, replacing the hardcoded "What's New" modal.

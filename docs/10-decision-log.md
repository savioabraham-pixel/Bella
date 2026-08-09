# 10 — Decision Log (ADRs)

Compressed ADRs: the decision, the reason, the cost, and what would change our mind. Status is
**Proposed** for all of these until reviewed.

---

**ADR-001 — Rewrite rather than incrementally refactor `index.html`**

The file has 15 competing style layers, ~212 globals, no module boundaries and no tests.
Refactoring in place means changing a 315 KB file with no safety net for a user group that
includes children and elderly relatives. Instead: freeze it as `legacy/index.html`, treat it as
the behavioural specification, and build alongside.
*Cost:* a period with two codebases. *Reconsider if:* the goal narrows to "fix security and
stop", in which case Phase 0 alone is the right scope.

---

**ADR-002 — Modular monolith, not microservices**

At 19–500 users, service boundaries cost distributed tracing, network failure modes and
deployment coordination while buying nothing. Module boundaries in one deployable give the same
design discipline at a fraction of the operational cost.
*Reconsider if:* voice or document processing develops a genuinely different scaling profile —
both are already queue-separated so extraction is a deployment change.

---

**ADR-003 — PostgreSQL as the single system of record**

The domain is relational; Postgres additionally covers vectors (pgvector), search (FTS) and
document-shaped data (JSONB), removing three services from the diagram. Google Sheets is
demoted to a read-only export target for the owner's existing workflow.
*Cost:* ~$70–100/month that a spreadsheet did not cost. *Reconsider if:* the workload becomes
overwhelmingly document-shaped, which it will not.

---

**ADR-004 — pgvector, not a dedicated vector database**

Expected volume is a few million vectors at most. HNSW in Postgres answers in single-digit
milliseconds there, keeps embeddings transactionally consistent with the rows they describe,
and makes user erasure one cascade instead of a two-system reconciliation.
*Reconsider at:* ~10M vectors, or when filtered-ANN recall degrades. The retrieval interface is
adapter-shaped so the swap touches one file.

---

**ADR-005 — Keep Firebase Auth as the identity provider**

Google sign-in and phone OTP already work, including reCAPTCHA and the returning-user path.
Rebuilding phone OTP is expensive and easy to get subtly wrong. The change is that the backend
now *verifies* the token instead of trusting a client-asserted email, and issues its own
session.
*Cost:* a dependency on Firebase and a second vendor alongside GCP (mild — same company).
*Reconsider if:* multi-tenant or enterprise SSO becomes a requirement.

---

**ADR-006 — Own session tokens rather than passing Firebase ID tokens through**

Firebase tokens are 1-hour, non-revocable-mid-life, and carry claims we do not control. Our own
15-minute access token plus a rotating refresh token gives immediate revocation, session
listing, family-level invalidation on reuse detection, and a role claim we own.
*Cost:* session table and rotation logic to maintain.

---

**ADR-007 — Python + FastAPI for the backend**

The AI-adjacent ecosystem — embeddings, rerankers, OCR, chunking, evaluation — is Python-first,
and the workload is I/O-bound where async FastAPI performs well. Pydantic gives one validation
layer across API, config and providers, and the generated OpenAPI keeps the TypeScript client
honest.
*Reconsider if:* the team is materially stronger in TypeScript — NestJS with the same
architecture is a legitimate substitute, paid for in the document pipeline.

---

**ADR-008 — Next.js for the frontend**

Routing conventions, first-class PWA support, streaming-friendly React, a public marketing and
legal surface from the same codebase, and the largest component ecosystem for the modal-heavy
UI this app has.
*Reconsider if:* the bundle budget becomes critical — a Vite SPA is meaningfully lighter and
otherwise adequate.

---

**ADR-009 — SSE for streaming, not WebSockets**

Traffic is one-directional. SSE passes proxies and Cloud Run without special handling,
reconnects natively, and works with `Last-Event-ID` resume backed by a Redis stream. WebSockets
add sticky-session and heartbeat concerns for no benefit here.
*Reconsider if:* real-time collaborative editing or duplex voice arrives.

---

**ADR-010 — All personal data moves out of the bundle into owner-scoped rows**

This is the central defect of the current system and the reason the rewrite is warranted, not
merely nice. Profiles, backgrounds, family context and follow-ups become `profiles` and
`relationships` rows readable only by their owner, with RLS underneath application scoping.
*Cost:* a migration that requires talking to 19 people about consent.

---

**ADR-011 — Retrieved memory, not concatenated memory**

Today every stored line is pasted into the system prompt. That grows linearly, dilutes
attention, and cannot express contradiction or decay. Replace with extract → dedupe → score →
retrieve top-k → decay, all user-visible and user-editable.
*Cost:* an embedding pipeline and a retrieval-quality metric to maintain.
*Benefit:* bounded cost, better recall, and a memory the user can actually correct.

---

**ADR-012 — Every provider behind an adapter; no SDK imported outside `providers/`**

The current backend is coupled to one model's payload shape, visible in the client's
`contents`/`candidates` handling. An interface plus a fallback per capability makes model
choice, cost routing and A/B testing configuration rather than refactoring.
*Cost:* a thin abstraction and the discipline to keep it thin — the adapter exposes the subset
we use, not every provider feature.

---

**ADR-013 — The system prompt is versioned data, not code**

`prompt_versions` rows with cohorts, activation and rollback, evaluated against a golden set
before promotion. Bella's persona is the product; changing it should be observable, reversible
and testable — not a 315 KB file diff.

---

**ADR-014 — Region `asia-south1` with a global edge**

Most users and the existing backend are in India, and keeping Indian personal data in-country
simplifies DPDP posture. Cloudflare in front gives US and UK family members edge termination
without a multi-region database.
*Reconsider if:* the user base tilts substantially to North America.

---

## Open questions for the owner

1. **Who owns this going forward?** The stack in [03](03-tech-stack.md) assumes at least one
   person comfortable with Python, TypeScript, SQL and GCP. If that is not the case, say so
   now — Supabase + Next.js is a smaller surface with real trade-offs, and it is better to
   choose it deliberately than to arrive there after a stalled migration.

2. **Is this staying a 19-person family app, or opening up?** The answer changes the calculus on
   HA, multi-region, cost ceilings and compliance depth. The architecture supports both; the
   spend does not need to.

3. **The git history.** Personal and medical details are in 174 public commits. Private
   repository, or history rewrite? Either way the 19 people should be told. This needs a
   decision before anything else ships.

4. **Consent for the biographies.** Health, bereavement and children's details are currently
   held without a consent record. Migrating them requires a conversation with each person.
   Who has it, and when?

5. **Budget.** ~$200–500/month is the honest range for the target. If the ceiling is lower,
   [07 §7](07-infra-devops.md#7-cost-model) lists what to trade and what it costs in resilience.

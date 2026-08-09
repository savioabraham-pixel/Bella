# 06 — Security & Privacy

Bella holds health information, bereavements, children's school details and home locations for
real, identifiable people who did not choose a threat model. That raises the bar above
"typical side project".

---

## 1. Threat model

| # | Threat | Today | After |
|---|---|---|---|
| T1 | Anyone reads all 19 people's biographies | ✅ Trivial — View Source (C1) | Data lives in Postgres, owner-scoped, RLS-enforced |
| T2 | Cross-tenant memory read | ✅ `POST /memory-read {profileKey}` (C2) | No client-asserted identity; subject from verified token |
| T3 | Free use of the paid model | ✅ Open `/chat` proxy (C3) | Auth + per-user rate limits + cost ceilings |
| T4 | Mail relay abuse | ✅ Client-supplied `to` (C4) | Server-pinned templates and recipients; SPF/DKIM/DMARC |
| T5 | Privilege escalation to admin | ✅ Edit one localStorage key (C5) | Role in the DB, checked server-side, all actions audited |
| T6 | XSS via model output or filename | ✅ 51 `innerHTML` sinks (H4) | React AST rendering, CSP, sanitiser, no `innerHTML` |
| T7 | Supply-chain via CDN | ✅ No SRI, no CSP (H5) | Bundled deps, lockfiles, SRI where CDNs remain, strict CSP |
| T8 | Prompt injection from documents / web results | Partially — a prompt instruction only | Structural: untrusted content delimited and labelled; tools require confirmation for side effects |
| T9 | Memory poisoning of another user | ✅ `/memory-write` takes any `profileKey` | Writes are owner-scoped and provenance-tracked |
| T10 | Session theft | localStorage tokens | HttpOnly rotating refresh, short access token, family revocation |
| T11 | Account takeover via SIM swap | Phone OTP is a full login path | OTP as second factor, not sole factor; re-auth for sensitive actions |
| T12 | Insider / operator access | Owner has full Sheets access | Least privilege, audited admin console, no raw DB browsing in prod |

---

## 2. Authentication & session

- **IdP:** Firebase Auth (Google + phone OTP) — retained.
- **Verification:** ID token signature checked against Google's JWKs (cached, refreshed on
  `kid` miss); audience, issuer, `exp`, `iat`, and `auth_time` all validated.
- **Session:** access JWT, 15 minutes, `HS256` on a rotating secret or `RS256` with JWKS;
  claims limited to `sub`, `sid`, `role`, `exp`.
- **Refresh:** opaque 256-bit token, SHA-256 hashed in `sessions`, HttpOnly + Secure +
  SameSite=Lax cookie, rotated on every use. Reuse detection revokes the entire token family.
- **Step-up auth:** re-verify OTP for deleting an account, exporting data, changing the phone
  number, or admin impersonation.
- **Firebase App Check** on the web app (reCAPTCHA Enterprise) so the API can reject tokens
  from non-app origins.
- **Authorised domains** restricted to production and preview hosts only.

## 3. Authorisation

Three layers, each independently sufficient:

1. **Route dependency** — `require_user()`, `require_role('admin')`, `require_owner(Thread)`.
2. **Query scoping** — every repository method takes `user_id`; there is no unscoped `get()`.
3. **Postgres RLS** — `SET LOCAL app.user_id` per request transaction; a forgotten `WHERE`
   returns zero rows rather than someone else's data.

A lint rule and a test fixture assert that no raw SQL against a user-owned table appears
outside the repository layer.

## 4. Application security

| Control | Implementation |
|---|---|
| CSP | `default-src 'self'; script-src 'self' 'nonce-…'; connect-src 'self' https://api.bella.app https://*.googleapis.com; img-src 'self' data: https:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'` |
| Other headers | HSTS with preload, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` limiting mic/camera/geo to self |
| XSS | No `innerHTML`, no `dangerouslySetInnerHTML`. Markdown → sanitised AST → React. |
| CSRF | Bearer tokens for the API; the refresh cookie is SameSite=Lax and its endpoint checks Origin |
| CORS | Explicit origin allowlist, credentials true, no wildcards |
| Input validation | Pydantic v2 on every request and response; length caps on all free text |
| Uploads | Size limit, MIME sniffed from bytes, malware scan before extraction, served only via short-lived signed URLs, never from an origin that can execute |
| SQL injection | SQLAlchemy parameter binding; raw SQL only in reviewed, parameterised repository methods |
| SSRF | The web-search and geocode tools use an allowlist; no user-supplied URL is fetched directly |
| Secrets | Secret Manager, injected at runtime; `gitleaks` in CI; **the current Firebase config in source is public by design and is not a leak** — App Check and domain restrictions are what protect it |
| Dependencies | Renovate, `pip-audit`, `npm audit`, SBOM per release, container scanning in Artifact Registry |

## 5. LLM-specific security

**Prompt injection.** Documents, web results and other users' text are untrusted. Mitigations:
untrusted content is wrapped in explicit delimiters and labelled in the prompt; tools that have
side effects (email, calendar, file write) require an explicit user confirmation step in the
UI, never a model decision alone; tool arguments are schema-validated before execution; and
retrieval never crosses a user boundary.

**Output handling.** Treat model output as untrusted input — render, never execute. No
`eval`, no dynamic `href` from model text without protocol validation, no auto-executed tool
chains.

**Cost & abuse.** Per-user daily token ceilings, per-request `max_tokens`, a circuit breaker on
the provider, and a monthly budget alert. Anomalous usage triggers a soft lock plus a notice.

**Data sent to providers.** Documented in the privacy policy: which provider, which region,
what retention. Prefer zero-retention or opt-out endpoints. Redact high-sensitivity memories
from any provider call that does not require them.

## 6. Privacy & compliance

Users are in India, the US and the UK, so **India's DPDP Act 2023**, **UK GDPR** and
**US state laws** all bear on this.

### Lawful basis and consent

The current app collects and stores health and bereavement details about named individuals with
no consent record. That is the most serious compliance gap. Required:

- A consent ledger (`consents` table) with versioned terms, per purpose:
  memory storage, voice recording, location, analytics, marketing.
- Granular and revocable. Revoking memory storage stops writes and offers deletion.
- Health, bereavement and children's data are `sensitivity='high'`: explicit opt-in, never
  surfaced proactively, excluded from digests and notifications, encrypted at the application
  layer if practical.
- **Children.** Two users are Grade 7 and one is 24 months old. Under DPDP, processing a
  child's data requires verifiable parental consent and prohibits behavioural targeting.
  Model this explicitly: `users.is_minor`, a `guardian_user_id`, guardian-granted consent, and
  reduced retention.

### Data subject rights

| Right | Implementation | SLA |
|---|---|---|
| Access / portability | `POST /privacy/export` → JSON + attachments ZIP | 7 days |
| Erasure | `POST /privacy/delete` → 7-day grace, then hard delete + cascade + object purge; audit rows retained under legal basis with the subject pseudonymised | 30 days |
| Rectification | Edit any memory or profile field in-app | Immediate |
| Restriction | Pause memory writes without deleting | Immediate |
| Objection | Opt out of extraction, keeping only explicit "remember this" | Immediate |

Erasure must also cover: GCS objects, Redis keys, provider-side logs where the provider offers
deletion, search indexes, and backups (documented as "purged at the next backup rotation, ≤35
days" — this must be stated, not glossed).

### Retention

| Data | Default | Configurable |
|---|---|---|
| Messages | Forever | 30 / 90 / 365 days / forever |
| Memories | Until deleted; unreferenced non-pinned decay at 180 days | Yes |
| Uploaded files | 90 days | 30 / 90 / forever |
| Recordings | 30 days | Yes |
| TTS cache | 30 days | No |
| Audit log | 2 years | No (compliance) |
| Access logs | 90 days | No |
| Backups | 35 days PITR | No |

### Documents to produce

Privacy policy, terms of use, a data-processing record, a cookie/storage notice, and — because
the app processes special-category data about identifiable individuals at scale relative to the
user base — a short **DPIA**. The current app links to Terms, Privacy and Security from the
account menu; all three are `onclick="closeUserMenu()"` stubs. They need real content before
anyone outside the family uses it.

## 7. Remediation before anything else

These four are independent of the rewrite and should ship on `main` now, in this order:

1. **Pull the PII out of the bundle.** Move `PROFILES`/`BACKGROUNDS`/`USER_CTX`/`FOLLOWUP`
   behind an authenticated backend fetch. Then treat the git history as compromised — the data
   is in 174 public commits. Options: rewrite history and force-push (breaks clones, and GitHub
   may retain unreferenced objects), or make the repository private and rotate what can be
   rotated. **Tell the 19 people either way** — several details are medical.
2. **Authenticate the backend.** Require a Firebase ID token on every endpoint; derive
   `profileKey` server-side from the verified `uid`. Reject any client-supplied identity.
3. **Move the system prompt server-side** and stop accepting a client-built `contents` array.
4. **Pin `/send-email`** to server-side templates and recipients.

Then: add a CSP, add SRI to the four CDN scripts, and stop rendering model output through
`innerHTML`.

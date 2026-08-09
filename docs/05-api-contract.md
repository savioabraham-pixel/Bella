# 05 — API Contract

Base: `https://api.bella.app/v1` · JSON in, JSON out (SSE for streams) · OpenAPI 3.1 generated
from FastAPI and used to codegen the TypeScript client.

## Conventions

**Auth.** Every endpoint except `/health`, `/auth/session` and `/auth/refresh` requires
`Authorization: Bearer <access_token>` (15-minute JWT). The refresh token is an HttpOnly,
Secure, SameSite=Lax cookie and is rotated on every use; reuse of a rotated token revokes the
whole session family.

**Client never asserts identity.** There is no `profileKey`, `userId` or `email` in any request
body. The subject comes from the verified token. This is the structural fix for finding C2.

**Errors.** One envelope, always:

```json
{ "error": { "code": "thread_not_found",
             "message": "No thread with that id.",
             "request_id": "01J8X...",
             "details": {} } }
```

Codes are stable strings; HTTP status carries the class. A resource belonging to another user
returns **404**, never 403 — a 403 confirms existence.

**Idempotency.** Every mutating endpoint accepts `Idempotency-Key`. Chat additionally uses
`client_message_id` in the body, unique per user.

**Pagination.** Cursor-based: `?limit=50&cursor=<opaque>` → `{items, next_cursor}`. No offsets.

**Rate limits.** Returned on every response as `X-RateLimit-Limit`, `-Remaining`, `-Reset`.
429 includes `Retry-After`.

**Versioning.** Path-versioned. A version is supported for 12 months after its successor ships.

---

## Auth

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/session` | Exchange a Firebase ID token for a Bella session |
| POST | `/auth/refresh` | Rotate the refresh cookie, return a new access token |
| POST | `/auth/logout` | Revoke this session |
| POST | `/auth/logout-all` | Revoke every session for the user |
| GET | `/auth/sessions` | List active sessions (device, IP region, last used) |

```http
POST /v1/auth/session
{ "id_token": "eyJhbGciOi...", "device_name": "Chrome on Pixel 8" }

200
{ "access_token": "eyJ...", "expires_in": 900,
  "user": { "id": "...", "display_id": "BE-12345678", "preferred_name": "Savio",
            "role": "member", "locale": "en-IN", "timezone": "Asia/Kolkata",
            "onboarding_complete": true },
  "flags": { "voice_v2": true, "documents_v2": false } }
```

## Profile

| Method | Path | Purpose |
|---|---|---|
| GET/PATCH | `/me` | Read/update profile, preferred name, pronunciation, locale, timezone |
| POST | `/me/avatar` | Presigned avatar upload |
| GET/POST | `/me/relationships` | Family/friends Bella may ask about |
| PATCH/DELETE | `/me/relationships/{id}` | |
| GET/PATCH | `/me/preferences` | Theme, voice on/off, notifications, retention |
| GET | `/me/quotas` | `{memories: {used, limit}, threads: {...}, storage: {...}}` |
| GET/POST | `/me/consents` | Consent ledger |

## Threads & messages

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/threads` | List (cursor) / create |
| GET/PATCH/DELETE | `/threads/{id}` | Read, rename, pin, archive, delete |
| GET | `/threads/{id}/messages` | Paginated history — **server-side, complete** |
| POST | `/threads/{id}/messages` | Send a turn |
| POST | `/threads/{id}/messages/{mid}/cancel` | Stop generation |
| POST | `/threads/{id}/messages/{mid}/regenerate` | Retry, optionally with a different model |
| POST | `/threads/{id}/summarise` | On-demand summary |
| GET | `/search?q=…&scope=threads\|messages\|memories` | Postgres FTS, replaces client substring scan |

### Sending a turn

```http
POST /v1/threads/{id}/messages
{ "content": "What did the lab report say about the second marker?",
  "attachments": ["file_01J8..."],
  "client_message_id": "c_01J8XQ...",
  "options": { "voice": true, "stream": true, "language": "auto" } }

202
{ "message_id": "01J8XR...", "assistant_message_id": "01J8XS...",
  "stream_url": "/v1/threads/01J8.../stream?message_id=01J8XS..." }
```

The turn is **persisted before the model is called**. If the process dies mid-generation, the
message exists with `status='failed'` and can be regenerated — instead of vanishing.

### Streaming

```http
GET /v1/threads/{id}/stream?message_id=01J8XS...
Accept: text/event-stream
Last-Event-ID: 42          ← on reconnect

event: delta
id: 43
data: {"text":"The second marker"}

event: tool_call
id: 44
data: {"tool":"web_search","status":"running","label":"Searching…"}

event: done
id: 51
data: {"message_id":"01J8XS...","finish_reason":"stop","tokens":{"in":2140,"out":318},
       "audio_url":"/v1/audio/01J8XT...","suggestions":["…","…"]}
```

Events: `delta`, `tool_call`, `tool_result`, `error`, `done`. Deltas are mirrored into a Redis
stream, so `Last-Event-ID` resumes exactly where the connection dropped. Heartbeat comment
every 15 s keeps proxies from closing the connection.

## Memory

| Method | Path | Purpose |
|---|---|---|
| GET | `/memories?kind=&q=&cursor=` | The "My Memory" screen — real data, not a promise |
| POST | `/memories` | Explicit "remember this" |
| PATCH/DELETE | `/memories/{id}` | Edit, pin, correct, delete |
| POST | `/memories/{id}/pin` | Never decays |
| GET | `/memories/{id}/provenance` | Which message produced this fact |

Extraction is server-side and model-driven, replacing the substring test in
`detectMemoryRequest()` (finding M4). The user sees, edits and deletes everything Bella
believes about them.

## Files

| Method | Path | Purpose |
|---|---|---|
| POST | `/files/presign` | `{filename, mime_type, size_bytes}` → signed GCS PUT + `file_id` |
| POST | `/files/{id}/commit` | Trigger scan → extract → chunk → embed |
| GET | `/files/{id}` | Status and metadata |
| GET | `/files/{id}/content` | Short-lived signed download URL |
| DELETE | `/files/{id}` | Delete object + chunks + embeddings |

Uploads never traverse the API. Limits: 50 MB per file, 10 files per turn, 2 GB per user.
MIME is sniffed from content; the extension is advisory only.

## Voice

| Method | Path | Purpose |
|---|---|---|
| POST | `/voice/tts` | `{text, lang, voice_id}` → `{audio_url, cached}`; cache key = hash(text+voice+lang) |
| POST | `/voice/stt` | Multipart audio → transcript (the fallback where Web Speech is absent) |
| GET | `/voice/voices` | Available voices per language |
| POST | `/recordings` | Register a client recording; presigned upload; optional transcription |

## Tools

Called by the model, not the client, but exposed for the converter UI:

| Method | Path | Purpose |
|---|---|---|
| GET | `/tools/fx?from=&to=&amount=` | Server-cached rates (5-min TTL) with a provider fallback |
| GET | `/tools/timezone?from=&to=&time=` | IANA-correct, DST-aware |
| GET | `/tools/units?…` | |
| GET | `/tools/geocode?lat=&lon=` | Server-side reverse geocode with a compliant User-Agent |
| GET | `/tools/gifs?q=` | Proxied, key server-side |

## Privacy

| Method | Path | Purpose |
|---|---|---|
| POST | `/privacy/export` | Queue a full export → signed ZIP when ready |
| POST | `/privacy/delete` | Deletion request; returns `due_at` |
| DELETE | `/privacy/delete` | Cancel within the grace window |
| GET | `/privacy/requests` | Status of outstanding requests |

## Admin (role ≥ admin, every call written to `audit_log`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/users` | Search, status, quota |
| POST | `/admin/users/{id}/impersonate` | Time-boxed, reason required, user-notified, audited |
| GET/POST | `/admin/prompts` | Prompt versions, activation, rollback |
| GET/PATCH | `/admin/flags` | Feature flags |
| GET | `/admin/usage` | Cost and token usage by user, model, day |
| GET | `/admin/data-requests` | Export/deletion queue |

Replaces the client-side "test mode" of finding C5. Impersonation is a server-authorised,
audited, notified operation — not an `if` statement comparing a localStorage email.

## Health

`GET /health` (liveness) · `GET /health/ready` (DB, Redis, provider reachability) ·
`GET /health/deep` (admin only; per-dependency latency).

---

## Rate limits (initial)

| Class | Limit |
|---|---|
| Chat turns | 30/min, 500/day per user |
| TTS characters | 20,000/day per user |
| File uploads | 50/day, 2 GB total per user |
| Auth attempts | 10/min per IP, 5/min per phone |
| Search | 60/min per user |
| Global per IP | 300/min |

Every limit is DB-configurable per user so a demo or a heavy family member can be raised
without a deploy.

---

## Contract changes from today

| Today | Target |
|---|---|
| Client builds and posts the whole system prompt | Server assembles it from `prompt_versions` + DB |
| `{profileKey}` identifies the user | Verified token subject; no client-asserted identity |
| No auth header anywhere | Bearer token on every call |
| One-shot response, no streaming | SSE with resume |
| Client-supplied email recipients | Server-pinned templates and recipients |
| History in localStorage, wiped on tab close | Server-side, paginated, durable |
| Search = substring scan over loaded threads | Postgres FTS across all threads |
| Silent failure (`catch{}` everywhere) | Typed errors with codes and request IDs |

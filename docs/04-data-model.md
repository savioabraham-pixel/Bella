# 04 — Data Model

PostgreSQL 16 with `pgcrypto`, `vector`, `pg_trgm`, `citext`. All timestamps are
`timestamptz`, all IDs are UUIDv7 (time-ordered — good index locality, no sequence leakage).
Every user-owned table carries `user_id` and is covered by Row-Level Security.

---

## 1. Identity & profile

```sql
CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  firebase_uid       text UNIQUE NOT NULL,
  email              citext UNIQUE,
  email_verified     boolean NOT NULL DEFAULT false,
  phone_e164         text UNIQUE,
  phone_verified     boolean NOT NULL DEFAULT false,
  display_id         text UNIQUE NOT NULL,           -- 'BE-12345678', shown in support
  status             text NOT NULL DEFAULT 'active'  -- active|suspended|deletion_requested|deleted
                     CHECK (status IN ('active','suspended','deletion_requested','deleted')),
  role               text NOT NULL DEFAULT 'member'  -- member|admin|owner
                     CHECK (role IN ('member','admin','owner')),
  locale             text NOT NULL DEFAULT 'en-IN',
  timezone           text NOT NULL DEFAULT 'Asia/Kolkata',
  created_at         timestamptz NOT NULL DEFAULT now(),
  last_seen_at       timestamptz,
  deleted_at         timestamptz
);

-- Sensitive personal data separated from the identity row: different retention,
-- different access controls, and it can be encrypted or dropped independently.
CREATE TABLE profiles (
  user_id            uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  full_name          text,
  preferred_name     text,                            -- what Bella calls them
  pronunciation      text,                            -- IPA or phonetic respelling
  pronouns           text,
  gender             text,
  date_of_birth      date,
  avatar_object      text,                            -- GCS object key, not a URL
  bio                text,                            -- was the hardcoded BACKGROUNDS blob
  group_name         text,                            -- was PROFILES[].group
  tone_preferences   jsonb NOT NULL DEFAULT '{}',
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Replaces the hardcoded FOLLOWUP map and the family knowledge baked into USER_CTX.
CREATE TABLE relationships (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  related_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,  -- if they are also a user
  name               text NOT NULL,
  relation           text NOT NULL,                   -- spouse|child|parent|sibling|friend|colleague
  notes              text,
  ask_about          boolean NOT NULL DEFAULT true,   -- include in the family follow-up
  sensitivity        text NOT NULL DEFAULT 'normal'
                     CHECK (sensitivity IN ('normal','high')),
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON relationships (user_id) WHERE ask_about;
```

> **This is the fix for finding C1.** The 19 hardcoded profiles become rows, readable only by
> their owner. Nothing personal ships in the bundle.

```sql
CREATE TABLE sessions (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token_hash bytea NOT NULL,                  -- SHA-256; never store the token
  family_id          uuid NOT NULL,                   -- rotation lineage; reuse ⇒ revoke family
  user_agent         text,
  ip_hash            bytea,                           -- hashed, not raw
  created_at         timestamptz NOT NULL DEFAULT now(),
  expires_at         timestamptz NOT NULL,
  revoked_at         timestamptz
);
CREATE INDEX ON sessions (user_id) WHERE revoked_at IS NULL;
```

---

## 2. Conversations

```sql
CREATE TABLE threads (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title              text NOT NULL DEFAULT 'New Conversation',
  title_generated    boolean NOT NULL DEFAULT false,
  summary            text,                            -- rolling summary of turns beyond the window
  summary_upto_seq   integer NOT NULL DEFAULT 0,
  message_count      integer NOT NULL DEFAULT 0,
  last_message_at    timestamptz,
  pinned             boolean NOT NULL DEFAULT false,
  archived_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON threads (user_id, last_message_at DESC) WHERE archived_at IS NULL;

CREATE TABLE messages (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  thread_id          uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- denormalised for RLS
  seq                integer NOT NULL,                -- monotonic within a thread
  role               text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  content            text NOT NULL,
  content_tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  status             text NOT NULL DEFAULT 'complete'
                     CHECK (status IN ('pending','streaming','complete','failed','cancelled')),
  client_message_id  text,                            -- idempotency key from the client
  -- provider bookkeeping
  model              text,
  prompt_version_id  uuid REFERENCES prompt_versions(id),
  input_tokens       integer,
  output_tokens      integer,
  cost_micros        bigint,
  latency_ms         integer,
  finish_reason      text,
  tool_calls         jsonb,
  error              jsonb,
  language           text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (thread_id, seq),
  UNIQUE (user_id, client_message_id)                 -- replay protection (finding: double-send)
);
CREATE INDEX ON messages (thread_id, seq);
CREATE INDEX ON messages USING GIN (content_tsv);     -- replaces client-side substring search
```

`message_attachments` joins messages to `files`. Audio replies are recorded in
`message_audio (message_id, object_key, voice_id, lang, duration_ms, provider, cached bool)` so
TTS output is reused instead of re-synthesised.

**Retention.** Messages are kept indefinitely by default, with a per-user setting for 30/90/365
days or forever. This directly reverses the current behaviour where a tab close destroys the
conversation (finding H2).

---

## 3. Memory

```sql
CREATE TABLE memories (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind               text NOT NULL                    -- fact|preference|pronunciation|followup|event
                     CHECK (kind IN ('fact','preference','pronunciation','followup','event')),
  content            text NOT NULL,
  content_tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  embedding          vector(1536),
  embedding_model    text,                            -- pin it; needed to detect stale vectors
  source             text NOT NULL                    -- user_explicit|extracted|imported|admin
                     CHECK (source IN ('user_explicit','extracted','imported','admin')),
  source_message_id  uuid REFERENCES messages(id) ON DELETE SET NULL,
  confidence         real NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  salience           real NOT NULL DEFAULT 0.5,
  sensitivity        text NOT NULL DEFAULT 'normal'
                     CHECK (sensitivity IN ('normal','high')),
  pinned             boolean NOT NULL DEFAULT false,  -- user-pinned: never decays
  times_referenced   integer NOT NULL DEFAULT 0,
  last_referenced_at timestamptz,
  last_confirmed_at  timestamptz,
  superseded_by      uuid REFERENCES memories(id),
  expires_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz
);
CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops)
  WHERE deleted_at IS NULL AND superseded_by IS NULL;
CREATE INDEX ON memories USING GIN (content_tsv);
CREATE INDEX ON memories (user_id, kind) WHERE deleted_at IS NULL;
```

**Retrieval query** (hybrid, ~top-8 into the prompt):

```sql
WITH semantic AS (
  SELECT id, 1 - (embedding <=> $query_vec) AS sim
  FROM memories
  WHERE user_id = $1 AND deleted_at IS NULL AND superseded_by IS NULL
    AND (expires_at IS NULL OR expires_at > now())
  ORDER BY embedding <=> $query_vec
  LIMIT 50
),
lexical AS (
  SELECT id, ts_rank(content_tsv, plainto_tsquery('simple', $query_text)) AS rank
  FROM memories
  WHERE user_id = $1 AND deleted_at IS NULL AND content_tsv @@ plainto_tsquery('simple', $query_text)
  LIMIT 50
)
SELECT m.*,
       0.6 * COALESCE(s.sim, 0)
     + 0.2 * COALESCE(l.rank, 0)
     + 0.2 * m.salience                                        AS score
FROM memories m
LEFT JOIN semantic s ON s.id = m.id
LEFT JOIN lexical  l ON l.id = m.id
WHERE (s.id IS NOT NULL OR l.id IS NOT NULL)
ORDER BY m.pinned DESC, score DESC
LIMIT 8;
```

Quotas move server-side into `user_quotas (user_id, memories_used, memories_limit,
threads_used, threads_limit, storage_bytes_used, storage_limit_bytes)`, maintained by trigger.
This makes the progress meter accurate — fixing finding M2, where the bar reported thread
count while the copy described memories.

---

## 4. Files & knowledge

```sql
CREATE TABLE files (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  thread_id          uuid REFERENCES threads(id) ON DELETE SET NULL,
  filename           text NOT NULL,
  mime_type          text NOT NULL,                   -- sniffed server-side, not from extension
  size_bytes         bigint NOT NULL,
  sha256             bytea NOT NULL,                  -- dedupe identical uploads
  object_key         text NOT NULL,
  kind               text NOT NULL,                   -- document|image|audio|video|recording
  status             text NOT NULL DEFAULT 'uploading'
                     CHECK (status IN ('uploading','scanning','extracting','ready','failed','quarantined')),
  scan_result        text,
  page_count         integer,
  extracted_chars    integer,
  error              jsonb,
  expires_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON files (user_id, sha256) WHERE status = 'ready';

CREATE TABLE file_chunks (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  file_id            uuid NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chunk_index        integer NOT NULL,
  content            text NOT NULL,
  content_tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  embedding          vector(1536),
  embedding_model    text,
  page_from          integer,
  page_to            integer,
  token_count        integer,
  UNIQUE (file_id, chunk_index)
);
CREATE INDEX ON file_chunks USING hnsw (embedding vector_cosine_ops);
```

Documents are chunked and retrieved rather than truncated at 15,000 characters and pasted into
the prompt (finding M3). A 200-page PDF becomes answerable instead of silently clipped.

---

## 5. Prompts, tools, flags

```sql
CREATE TABLE prompt_versions (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  name          text NOT NULL,                        -- 'bella.system'
  version       integer NOT NULL,
  template      text NOT NULL,                        -- Jinja2 with declared variables
  variables     jsonb NOT NULL DEFAULT '[]',
  is_active     boolean NOT NULL DEFAULT false,
  cohort        text,                                 -- null = everyone
  notes         text,
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (name, version)
);

CREATE TABLE tool_invocations (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  message_id    uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tool_name     text NOT NULL,
  arguments     jsonb NOT NULL,
  result        jsonb,
  status        text NOT NULL,
  latency_ms    integer,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feature_flags (
  key           text PRIMARY KEY,
  enabled       boolean NOT NULL DEFAULT false,
  rollout_pct   integer NOT NULL DEFAULT 0 CHECK (rollout_pct BETWEEN 0 AND 100),
  user_allowlist uuid[] NOT NULL DEFAULT '{}',
  updated_at    timestamptz NOT NULL DEFAULT now()
);
```

---

## 6. Compliance & audit

```sql
CREATE TABLE consents (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind          text NOT NULL,          -- terms|privacy|memory_storage|voice_recording|location|analytics
  version       text NOT NULL,
  granted       boolean NOT NULL,
  granted_at    timestamptz NOT NULL DEFAULT now(),
  ip_hash       bytea,
  user_agent    text
);
CREATE INDEX ON consents (user_id, kind, granted_at DESC);

-- Append-only. No UPDATE or DELETE grant for the application role.
CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  actor_user_id uuid,
  actor_role    text,
  action        text NOT NULL,          -- login|memory.delete|admin.impersonate|export.request|…
  resource_type text,
  resource_id   uuid,
  target_user_id uuid,
  metadata      jsonb NOT NULL DEFAULT '{}',
  ip_hash       bytea,
  request_id    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (target_user_id, created_at DESC);
CREATE INDEX ON audit_log (action, created_at DESC);

CREATE TABLE data_requests (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('export','deletion','rectification')),
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','processing','completed','rejected','cancelled')),
  reason        text,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  due_at        timestamptz NOT NULL,                 -- SLA clock, visible to the user
  completed_at  timestamptz,
  artifact_key  text                                  -- GCS object for an export
);
```

The current delete-account flow writes a spreadsheet row and emails the owner. Here it creates
a `data_requests` row with an SLA and a state machine, and a scheduled job executes it.

---

## 7. Row-Level Security

Application code always filters by `user_id`; RLS is the backstop for the day it doesn't.

```sql
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY memories_owner ON memories
  USING (user_id = current_setting('app.user_id', true)::uuid);
-- identical policies on threads, messages, files, file_chunks, relationships, profiles
```

The API sets `SET LOCAL app.user_id = …` at the start of every request transaction. A missing
`WHERE user_id = …` then returns zero rows instead of another family member's memories.

---

## 8. Migration from Sheets

One-off, idempotent, re-runnable:

1. Snapshot every tab to CSV; keep the raw copies in a private bucket as the rollback source.
2. `Users` → `users` + `profiles`. Derive `firebase_uid` by looking each email up in Firebase;
   rows with no match land in a `staging_orphans` table for manual review rather than being
   dropped.
3. `Memories` → `memories` with `source='imported'`, `confidence=0.7`, embeddings generated in
   batch. Deduplicate at cosine ≥ 0.92 before insert.
4. `Pronunciations` → `profiles.pronunciation` (latest wins) and a `memories` row of kind
   `pronunciation` for provenance.
5. The 19 hardcoded `BACKGROUNDS`/`USER_CTX` blobs → `profiles.bio` and `relationships` rows,
   **entered with each person's knowledge and consent**. Do not import health or bereavement
   details silently; those need an explicit conversation and a `consents` row.
6. Verify: row counts, a per-user spot check, and a replay of 20 archived conversations through
   the new context builder compared against the old prompt output.
7. Keep the Sheets export job running for one month so the owner's existing workflow survives
   the cutover.

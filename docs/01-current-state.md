# 01 — Current State Analysis

## 1. Repository facts

| Question | Answer |
|---|---|
| Current branch (at time of analysis) | `main` |
| Branches | **One real branch: `main`.** `git branch -a` prints three lines, but two are the remote tracking ref `remotes/origin/main` and the symbolic ref `remotes/origin/HEAD -> origin/main`. There is one branch, mirrored on one remote. (`beta` is the second, created for these docs.) |
| Remote | `https://github.com/savioabraham-pixel/Bella` |
| Commits | 174 |
| Tracked files | 3 — `index.html`, `README.md`, `.gitignore` |
| `index.html` | 5,540 lines / 315 KB |
| Build / deps / tests / CI | None. No `package.json`, no lockfile, no test runner, no workflow. |
| Deployment | GitHub Pages from repo root on `main` |

Commit history is dominated by ~170 commits all titled `Update index.html via Claude Code`.
There is no semantic history: you cannot answer "when did OTP login land" or "what broke
threading" from `git log`. Every change is a whole-file diff on a 315 KB file, so `git blame`
and bisect are effectively unusable.

## 2. Anatomy of `index.html`

```
lines    1 –   24   <head>: meta, PWA manifest as a data: URI, 4 CDN <script> tags
lines   25 –  908   14 separate <style> blocks
lines  909 –  910   Firebase compat SDK (app + auth), v10.8.0
lines  912 – 1438   <body> markup — splash, sidebar, topbar, converter, chat, 6 modals
lines 1439 – 5444   the application: ~212 functions in one global scope
lines 5447 – 5510   a 15th <style> block of !important overrides
lines 5513 – 5537   a trailing DOMContentLoaded script that mutates .file-chip nodes
```

### The CSS is fifteen layers of override

The style blocks carry names that describe their own history:
`dark-structural-fix`, `dark-precise-no-glow`, `v77-lhs-button-fixed`, `v82-final-system`,
`v95-plus-deep-fix`. Later blocks undo earlier ones with `!important`. On top of that, most
markup carries long inline `style="..."` attributes and inline `onmouseover` handlers, so the
cascade has four competing sources. The README's advice to "change theme colours in `:root`"
holds for the variables but not for the many hardcoded `#0B2545` / `#D4AF37` literals scattered
through markup and JS template strings.

### The JavaScript is one global scope

All ~212 functions are globals, wired to the DOM through inline `onclick=` attributes. There
are no modules, no classes, and no separation between rendering, state, network and business
logic. Cross-cutting state lives in bare `let` bindings (`cfg`, `threads`, `currentThreadId`,
`idState`, `userMemory`, `pFiles`/`pTexts`/`pImages`) plus ad-hoc `window._sending`,
`window._recording`, `window._mediaRecorder`.

### Subsystems present

| Subsystem | Implementation |
|---|---|
| Identity | Firebase Auth — Google popup + phone/OTP via invisible reCAPTCHA; multi-step registration gate with photo crop/zoom editor |
| Personalisation | 19 hardcoded profiles + per-profile background, follow-up question, and "world" context, assembled by `buildSystemPrompt()` into a ~4 KB system prompt |
| Chat | `send()` → `callBella()` → `POST /chat`; Gemini-shaped payload (`contents`/`parts`/`inlineData`, response read from `candidates[0].content.parts[0].text`) |
| Memory | `/memory-read` and `/memory-write` with types `user`, `memory`, `pronunciation`, `followup`; caps of 100 memories / 50 threads |
| Threads | In-memory `threads{}` object, mirrored to `localStorage.b_threads` (last 20 messages each) |
| Voice out | `/speak` → audio blob → `new Audio()`; ElevenLabs behind the backend |
| Voice in | Web Speech API (`SpeechRecognition`), push-to-talk plus continuous mode |
| Recording | `MediaRecorder` for mic / screen / camera; "save to Drive" is a stub |
| Documents | Client-side extraction: pdf.js, mammoth, SheetJS, JSZip+regex for PPTX; images to base64 inline parts; max 10 files, text truncated at 15,000 chars each |
| Utilities | Currency (live rates from `open.er-api.com`), units, timezones (63 cities), emoji picker, GIF search via `/gif-search`, reverse geocoding via Nominatim |
| Admin | Client-side email comparison against a hardcoded Gmail address unlocks a profile-impersonation "test mode" |
| Account | Feedback panel, delete-account request → `/sheets-write` + `/send-email` to the owner |

### External dependencies (all unpinned at runtime, none integrity-checked)

`cdnjs`: mammoth 1.6.0, xlsx 0.18.5, pdf.js 3.11.174, jszip 3.10.1 · `gstatic`: firebase 10.8.0 ·
`fonts.googleapis.com` · direct third-party calls to `open.er-api.com` and
`nominatim.openstreetmap.org` · backend `https://bella-backend-*.asia-south1.run.app`.

---

## 3. Findings

Ordered by severity. Everything below was read out of the file, not inferred.

### 🔴 Critical

**C1 — Nineteen real people's private lives are compiled into a publicly served file.**
`PROFILES`, `BACKGROUNDS`, `FOLLOWUP` and `USER_CTX` (lines 1451–1540) contain full names,
family relationships, employers and job titles, children's schools and grades, home
neighbourhoods, cars, and — most seriously — health and bereavement details: a recent eye
surgery, a father's recovery from critical illness, and a bereavement flagged with "NEVER
mention unless she does". This is special-category personal data. `index.html` is served
unauthenticated from GitHub Pages; anyone can read all of it with View Source, and the full
history of edits to it is in the public git log. Removing it from the current file does not
remediate the exposure — it remains in every historical commit.

**C2 — Backend endpoints are unauthenticated and keyed on a guessable identifier.**
No call attaches a Firebase ID token or any credential. `POST /memory-read` takes
`{profileKey}` and returns that user's stored memories. `profileKey` is derived
deterministically from the email (`email.replace('@gmail.com','').replace(/\./g,'_')`), so it
is guessable for anyone whose Gmail you know. Cross-tenant read of long-term memory is a
single curl away. `/memory-write` accepts arbitrary memory text for any `profileKey` —
this is also a prompt-injection channel into another user's future sessions.

**C3 — `/chat` accepts the entire prompt from the client.**
`callBella()` builds the system prompt in the browser and posts the whole `contents` array.
The backend is a thin, open proxy to a paid model. Any third party can send arbitrary prompts
at the project's expense, strip every safety instruction, and use it as a free model endpoint.
The client-side prompt is also fully visible to end users.

**C4 — `/send-email` is reachable with a client-supplied recipient.**
The welcome-email path posts `to`, `subject` and `body` from the browser. If the backend does
not pin the recipient and template server-side, it is an open relay sending from the project's
address — a spam and phishing vector that will get the sending domain blocked.

**C5 — The admin gate is client-side.**
`send()` reads `localStorage.b_registered_user.email` and compares it to a hardcoded address.
Editing one localStorage value unlocks "test mode", which rebuilds the system prompt as any of
the 19 profiles. The client also controls what it sends to `/chat`, so nothing server-side
stops it.

### 🟠 High

**H1 — `switchThread()` throws on every call.** Lines 1964–1965 write to
`#topbar-thread` and `#topbar-sub`. Those elements were removed when the topbar was rebuilt —
line 3250 even comments "topbar-thread removed". Neither `id` exists in the markup. The
uncaught `TypeError` aborts the function before `renderThreadList()` and `saveThreadsLocal()`,
and aborts `newThread()` before its greeting timer is scheduled. Consequence: switching a
thread leaves the sidebar highlight stale and skips the save; creating a new conversation
silently produces no greeting. `startRename()` (1973) hits the same wall.
`checkSummariseBtn()` (3849) guards with `if(sub&&…)` and survives.

**H2 — Conversation history is destroyed by design.** The `pagehide` handler (5427)
removes `b_threads` and `b_registered_user` on tab close, and only the last 20 messages per
thread were ever written. Combined with a Sheets-backed memory store that holds only extracted
"memory" lines, closing a tab loses the conversation permanently. The UI, meanwhile, shows a
50-thread quota and a persistence promise ("Conversations stored securely to your account").

**H3 — Google Sheets is the database.** No transactions, no constraints, no indexes, no
concurrent-write safety, ~10M cell and per-minute API quota ceilings, and O(n) reads. Two
sessions writing memory concurrently can interleave rows or clobber. Every product feature
that needs a query — search, analytics, retention, export, deletion — has no primitive to
build on.

**H4 — DOM XSS surface.** 51 `innerHTML` assignments, several interpolating values that
originate outside the code: thread previews (`t.hist[…].text`, unescaped — only `t.name` is
escaped), uploaded filenames in `addFileChip()`, the recording filename and blob URL injected
into an `onclick="saveRecordingToDrive('${url}','${fname}')"` string, delete-account modal
fields read from localStorage, and model output rendered by `bellaMsg()`. A model reply or a
crafted filename can execute script. There is no CSP header and no sanitiser.

**H5 — Supply chain is unprotected.** Four CDN scripts with no `integrity=` attribute and no
CSP. A cdnjs compromise, or a DNS/TLS interception, executes attacker code in a page that
holds Firebase auth state. `grep -c "integrity="` → 0; `grep -c "Content-Security-Policy"` → 0.

### 🟡 Medium

**M1 — Documented PWA does not exist.** The README describes an installable PWA with a service
worker; `grep serviceWorker index.html` returns nothing. The manifest is a `data:` URI with
`start_url: /Bella/`. Result: no offline capability, no install prompt on most browsers, no
caching.

**M2 — The memory meter lies.** `updateMemoryProgress()` computes the percentage purely from
thread count and passes a hardcoded `0` for `memCount`, yet `_showMemAlert()` renders copy
about "memories". The 100-memory cap is never actually measured client-side.

**M3 — Prompt context grows without bound.** `callBella()` sends the full `getHist()` every
turn plus a ~4 KB system prompt plus up to 10 documents × 15,000 chars. Cost and latency scale
linearly with conversation length until the model's context window rejects it; there is no
summarisation, truncation or token budget.

**M4 — Feature detection by keyword.** `isTimeSensitive()` toggles web search on substring
matches including the bare words `just`, `kal` and `price`. `detectMemoryRequest()` fires on
any occurrence of `remember`, then stores the message with those words stripped — so "I don't
remember where I parked" is persisted as a long-term fact.

**M5 — Fragile identity parsing.** `handleIdentityInput()` is eight regexes plus a noise-word
denylist plus "take the first word". `matchName()` and the `AMBIGUOUS_NAMES` list encode a
disambiguation rule (two people named Sreya) that belongs in data, not control flow.

**M6 — Blocking browser dialogs.** `prompt()` for rename, `confirm()` for delete, `alert()` for
recording errors. These are unstyled, unusable in installed-PWA contexts, and blocked in some
embedded webviews.

**M7 — Dead and duplicated code.** `openVoiceRecording()` clicks `#mic-btn`, which does not
exist (the fallback message saves it). `const _origNewThread=newThread;` (3632) is assigned and
never used. `TENOR_KEY=''` is a leftover. The "what do you know about me" instruction block in
`buildSystemPrompt()` is duplicated verbatim (lines 1561 and 1563). `autoPlay()` is unreferenced.

**M8 — No accessibility layer.** Icon-only buttons without `aria-label`, custom toggles built
from `div`s with no role or keyboard handling, modals without focus trap or `aria-modal`,
`title=` used as the only label. Colour contrast on 8–10 px `--tsoft` text is below WCAG AA.

**M9 — Everything is single-region and single-instance.** One Cloud Run service in
`asia-south1` for chat, TTS, memory, email, sheets and GIFs. Any dependency outage takes down
all of them. No rate limiting is visible from the client contract, so one user's document
upload loop can exhaust the quota for the whole family.

### 🔵 Low

- Hardcoded owner email `savioabraham.miiraki@gmail.com` in application logic (3927, 3589).
- Currency rates fetched from a keyless third party with no cache and no fallback.
- Nominatim used for reverse geocoding without the User-Agent its usage policy requires.
- `en-IN` and `Asia/Kolkata` assumptions in date formatting, sent to users in the US and UK.
- Timezone abbreviations stored as static strings (`EST`, `AEDT`) that are wrong half the year;
  `_tzOffsetMin()` computes correctly, so the two disagree.
- The Firebase Web API key at line 2039 is public by design — not a leak, but it makes
  authorised-domain restrictions and App Check mandatory rather than optional.

---

## 4. What is genuinely good here

Worth preserving through the rewrite, because it is the actual product:

- **The persona works.** The system prompt is specific, disciplined about tone, and encodes
  real relationship knowledge. It is the differentiator; treat it as a versioned asset.
- **Credentials are already server-side.** A previous refactor pulled the Sheets OAuth client,
  the GIPHY key and the model key out of the browser. The pattern is right.
- **The identity → memory → greeting loop is a real feature** and is more thought-through than
  most assistant demos.
- **The registration flow** (Google, then phone/OTP, then profile with photo crop) is complete
  and covers returning users and "not you?".
- **Multilingual intent** — English, Hindi, Bengali, Marathi in both directions — is designed
  in, not bolted on.
- **Zero-friction deployment.** Whatever replaces it should keep "push and it is live" as a
  hard requirement.

---

## 5. The one-line diagnosis

Bella is a **well-designed product trapped in a delivery mechanism that cannot carry it**:
personal data is in the bundle instead of a database, authorisation is in the client instead of
the server, and the system of record is a spreadsheet. The next document describes the target.

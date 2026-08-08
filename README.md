# Bella

A personal AI companion for a small circle of family and friends — professional executive assistance wrapped in a warm, familiar personality.

**Live:** https://savioabraham-pixel.github.io/Bella/

---

## What it is

Bella is a voice-and-chat assistant that recognises who is talking to her, remembers past conversations, reads documents you hand her, and speaks her answers back. She is built as a **single self-contained `index.html`** — no build step, no bundler, no framework. Open the file and it runs.

Installable as a PWA (standalone display, custom icons, theme colour), so it behaves like a native app on phone and desktop.

## Architecture

```
index.html  ─┬─ UI + all application logic (vanilla JS)
             ├─ Firebase Auth ......... Google sign-in + phone/OTP
             ├─ Cloud Run backend ..... chat, memory, speech, email, GIFs
             ├─ Google Sheets API ..... optional persistent store
             └─ CDN libraries ......... document parsing
```

Everything client-side lives in one file. Model access, secrets, and persistence sit behind a private Cloud Run service, so no model credentials ship to the browser.

### Backend endpoints

| Endpoint | Purpose |
|---|---|
| `/chat` | Conversation turns, with an optional web-search flag |
| `/memory-read` · `/memory-write` | Per-user long-term memory, pronunciation fixes, follow-ups |
| `/speak` | Text-to-speech |
| `/send-email` | Outbound email |
| `/sheets-write` | Append to the Google Sheets store |
| `/gif-search` | GIF lookup |

## Features

**Who you are**
- Firebase Authentication — Google sign-in, and phone number with OTP via reCAPTCHA
- Named profiles, each with its own background and conversational context
- A returning-user path that skips registration, and a "not you?" switch

**Memory**
- Long-term memory per user, persisted server-side and injected into the system prompt
- Pronunciation corrections that stick — correct her once and she keeps it
- Capped at 100 memories and 50 threads per user, with a progress indicator
- Conversation threads with search, rename, and delete

**Voice**
- Speech recognition for input (Web Speech API)
- Spoken replies through the `/speak` endpoint
- A continuous hands-free mode alongside push-to-talk

**Documents**
Attach up to 10 files at once and Bella reads them in the browser:

| Format | Library |
|---|---|
| PDF | `pdf.js` |
| Word (`.docx`) | `mammoth.js` |
| Excel (`.xlsx`) | `SheetJS` |
| PowerPoint (`.pptx`) | `JSZip` + XML extraction |

**Other**
- Light and dark themes driven entirely by CSS variables — retheme by editing `:root`
- Live currency conversion, reverse geocoding for location context, GIF search
- Contextual follow-up suggestion chips generated from the last reply
- Responsive down to mobile, with iOS safe-area handling

## Running locally

No install, no dependencies:

```bash
git clone https://github.com/savioabraham-pixel/Bella.git
cd Bella
python3 -m http.server 8000
```

Then open `http://localhost:8000`. A server is needed rather than opening the file directly — Firebase Auth and the service worker both require an `http(s)` origin.

Sign-in requires your local origin to be listed as an authorised domain in the Firebase console.

## Deploying

`main` deploys automatically to GitHub Pages from the repository root. Push to `main` and the live site follows within about a minute.

## Notes for editors

- `index.html` is ~5,500 lines. The CSS block at the top is organised by screen (splash, setup, chat, input, mobile overrides); application logic follows in `<script>` blocks.
- Theme colours are centralised in the `:root` and `body.dark` variable blocks — change them there rather than hunting hex codes.
- Conversation history sent to the model is **not** truncated, while only the last 20 messages per thread are written to `localStorage`. A long conversation therefore loses most of its context on page reload.
- Client state lives under `b_*` keys in `localStorage` (threads, theme, registration, preferences).

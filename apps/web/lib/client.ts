/**
 * Browser API client.
 *
 * Talks to the API directly from the browser rather than proxying through Next.js: the
 * reply is a stream, and putting a server hop in the middle means buffering it.
 *
 * Streaming uses `fetch` with a `ReadableStream` rather than `EventSource`. `EventSource`
 * cannot set an `Authorization` header — it only carries cookies — and the access token is
 * deliberately not a cookie. The cost is parsing SSE frames by hand, which is thirty lines;
 * the alternative is putting a bearer token in a query string, where it lands in logs.
 */

/**
 * Where the API lives.
 *
 * Same-origin, always. `next.config.ts` rewrites `/api/v1/*` to the API container, which
 * settles four things at once that a direct cross-origin call gets wrong:
 *
 *   - no CORS preflight, and no origin allowlist to keep in step with the URL bar;
 *   - the page's own CSP is `connect-src 'self'`, which a cross-origin call violates;
 *   - `localhost` resolves to both ::1 and 127.0.0.1, and a hard-coded spelling breaks
 *     whichever one the user did not type;
 *   - the refresh cookie stays first-party.
 *
 * A deployment that serves the API on its own hostname sets NEXT_PUBLIC_API_URL and adds
 * that origin to the CSP.
 */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const V1 = API_BASE ? `${API_BASE}/v1` : "/api/v1";

export interface AuthenticatedUser {
  id: string;
  display_id: string;
  preferred_name: string | null;
  role: string;
  locale: string;
  timezone: string;
  onboarding_complete: boolean;
}

export interface SessionResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthenticatedUser;
}

export interface Thread {
  id: string;
  title: string;
  title_generated: boolean;
  pinned: boolean;
  archived: boolean;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
}

export type MessageStatus = "pending" | "streaming" | "complete" | "failed" | "cancelled";

export interface Message {
  id: string;
  thread_id: string;
  seq: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  status: MessageStatus;
  model: string | null;
  finish_reason: string | null;
  created_at: string;
}

export interface SearchHit {
  thread_id: string;
  thread_title: string;
  message_id: string | null;
  seq: number | null;
  role: string | null;
  /** Contains <b> tags around the matched terms, from Postgres ts_headline. */
  snippet: string;
  created_at: string;
}

export interface Accepted {
  message_id: string;
  assistant_message_id: string;
  seq: number;
  stream_url: string;
  idempotent_replay: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** The API's error envelope, which every failure uses. */
interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

async function request<T>(
  path: string,
  token: string | null,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${V1}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
    // The refresh cookie is path-scoped to /v1/auth and must ride along on sign-in.
    credentials: "include",
  });

  if (!response.ok) {
    let code = `http_${response.status}`;
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as ErrorEnvelope;
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // A non-JSON failure — the status is all we have.
    }
    throw new ApiError(code, message, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * A development sign-in.
 *
 * The API runs with `AUTH_PROVIDER=mock` locally, which reads an unsigned token's claims
 * without verifying them. This builds one of the right shape. In every deployed
 * environment `AUTH_PROVIDER=firebase` and settings refuse to start otherwise, so this
 * path cannot be used to impersonate anyone anywhere real.
 */
function developmentIdToken(uid: string, name: string, phone?: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

  const now = Math.floor(Date.now() / 1000);
  return [
    encode({ alg: "HS256", typ: "JWT" }),
    encode({
      sub: uid,
      name,
      email: `${uid}@example.test`,
      email_verified: true,
      ...(phone ? { phone_number: phone } : {}),
      iat: now,
      exp: now + 3600,
      firebase: { sign_in_provider: "google.com" },
    }),
    "signature-not-checked-in-mock-mode",
  ].join(".");
}

export const api = {
  signIn(uid: string, name: string, phone?: string): Promise<SessionResponse> {
    return request<SessionResponse>("/auth/session", null, {
      method: "POST",
      body: JSON.stringify({
        id_token: developmentIdToken(uid, name, phone),
        device_name: "Browser",
      }),
    });
  },

  updateProfile(
    token: string,
    patch: Record<string, string | null>,
  ): Promise<{ preferred_name: string | null }> {
    return request("/me", token, { method: "PATCH", body: JSON.stringify(patch) });
  },

  listThreads(token: string): Promise<{ items: Thread[]; next_cursor: string | null }> {
    return request("/threads?limit=50", token);
  },

  createThread(token: string): Promise<Thread> {
    return request("/threads", token, { method: "POST", body: "{}" });
  },

  renameThread(token: string, threadId: string, title: string): Promise<Thread> {
    return request(`/threads/${threadId}`, token, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },

  summarise(token: string, threadId: string): Promise<{ summary: string | null }> {
    return request(`/threads/${threadId}/summarise`, token, { method: "POST" });
  },

  search(token: string, query: string): Promise<{ items: SearchHit[] }> {
    return request(`/search?q=${encodeURIComponent(query)}&limit=25`, token);
  },

  deleteThread(token: string, threadId: string): Promise<void> {
    return request(`/threads/${threadId}`, token, { method: "DELETE" });
  },

  listMessages(token: string, threadId: string): Promise<{ items: Message[] }> {
    return request(`/threads/${threadId}/messages?limit=100`, token);
  },

  send(token: string, threadId: string, content: string): Promise<Accepted> {
    return request(`/threads/${threadId}/messages`, token, {
      method: "POST",
      body: JSON.stringify({
        content,
        // Survives a double-tap on Send: the server returns the same turn rather than
        // billing for two.
        client_message_id: crypto.randomUUID(),
      }),
    });
  },

  cancel(token: string, threadId: string, messageId: string): Promise<Message> {
    return request(`/threads/${threadId}/messages/${messageId}/cancel`, token, {
      method: "POST",
    });
  },

  regenerate(token: string, threadId: string, messageId: string): Promise<Accepted> {
    return request(`/threads/${threadId}/messages/${messageId}/regenerate`, token, {
      method: "POST",
    });
  },
};

export interface StreamHandlers {
  onDelta(text: string): void;
  onDone(): void;
  onError(message: string): void;
}

/**
 * Consume an SSE reply.
 *
 * Returns an abort function. Aborting only drops *this reader* — the server keeps
 * generating, which is deliberate: closing a laptop lid should not discard the answer.
 * Stopping generation is a separate, explicit call to `api.cancel`.
 */
export function streamReply(
  token: string,
  streamUrl: string,
  handlers: StreamHandlers,
): () => void {
  const controller = new AbortController();

  void (async () => {
    try {
      // The server returns `/v1/threads/…/stream`; rebase it onto whichever prefix this
      // client is using rather than trusting the path to already be reachable.
      const url = `${V1}${streamUrl.replace(/^\/v1/, "")}`;
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        handlers.onError(`Stream failed (${response.status})`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Frames are separated by a blank line. Anything after the last separator is a
        // partial frame and stays in the buffer until the rest of it arrives.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          if (!frame.trim() || frame.startsWith(":")) continue; // heartbeat

          let event = "message";
          let data = "{}";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7);
            else if (line.startsWith("data: ")) data = line.slice(6);
          }

          const payload = JSON.parse(data) as { text?: string; message?: string };
          if (event === "delta" && payload.text) handlers.onDelta(payload.text);
          else if (event === "done") return handlers.onDone();
          else if (event === "error") {
            return handlers.onError(payload.message ?? "Something went wrong.");
          }
        }
      }
      handlers.onDone();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      handlers.onError(error instanceof Error ? error.message : "Connection lost.");
    }
  })();

  return () => controller.abort();
}

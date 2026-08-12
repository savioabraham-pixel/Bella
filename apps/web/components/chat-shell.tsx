"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  streamReply,
  type AuthenticatedUser,
  type Message,
  type Thread,
} from "@/lib/client";
import { Gate, type Registration } from "@/components/gate";
import { Composer } from "@/components/composer";
import { MessageList } from "@/components/message-list";
import { Sidebar } from "@/components/sidebar";
import { Splash } from "@/components/splash";
import { Topbar } from "@/components/topbar";
import { SearchOverlay } from "@/components/search-overlay";

const TOKEN_KEY = "bella.session";
const THEME_KEY = "bella.dark";
const SPLASH_KEY = "bella.splash-seen";

interface StoredSession {
  token: string;
  user: AuthenticatedUser;
}

/**
 * The chat surface.
 *
 * State lives here rather than in a store: there is one screen, and the only genuinely
 * moving part is a single in-flight reply. `streaming` holds the id of the assistant row
 * being written, which is what tells the composer to offer Stop instead of Send.
 */
export function ChatShell() {
  const [session, setSession] = useState<StoredSession | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [splash, setSplash] = useState(true);
  const [dark, setDark] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searching, setSearching] = useState(false);

  // Aborts the reader, not the generation. Stopping is a separate, explicit API call —
  // closing a laptop lid should not discard an answer that is already being paid for.
  const abortRef = useRef<(() => void) | null>(null);

  // ── session ────────────────────────────────────────────────────────────────
  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY);
    if (stored) {
      try {
        setSession(JSON.parse(stored) as StoredSession);
      } catch {
        localStorage.removeItem(TOKEN_KEY);
      }
    }
    // Dark mode is a class on <body>, which is what the ported stylesheet keys off.
    const storedDark = localStorage.getItem(THEME_KEY) === "1";
    setDark(storedDark);
    document.body.classList.toggle("dark", storedDark);

    // The splash is shown once per session, not on every navigation.
    setSplash(sessionStorage.getItem(SPLASH_KEY) !== "1");
    setRestoring(false);
  }, []);

  const toggleTheme = useCallback(() => {
    setDark((current) => {
      const next = !current;
      localStorage.setItem(THEME_KEY, next ? "1" : "0");
      document.body.classList.toggle("dark", next);
      return next;
    });
  }, []);

  const dismissSplash = useCallback(() => {
    sessionStorage.setItem(SPLASH_KEY, "1");
    setSplash(false);
  }, []);

  const signOut = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setSession(null);
    setThreads([]);
    setThreadId(null);
    setMessages([]);
  }, []);

  const register = useCallback(async (registration: Registration) => {
    setSigningIn(true);
    setAuthError(null);
    try {
      const response = await api.signIn(
        registration.uid,
        registration.preferredName,
        registration.phone,
      );
      const next = { token: response.access_token, user: response.user };

      // The gate collected more than a token can carry. Persisting it here means the
      // first greeting already uses the right name.
      const profile = await api.updateProfile(next.token, {
        full_name: `${registration.firstName} ${registration.lastName}`,
        preferred_name: registration.preferredName,
        gender: registration.gender,
        date_of_birth: registration.dateOfBirth,
      });
      next.user = { ...next.user, preferred_name: profile.preferred_name };

      localStorage.setItem(TOKEN_KEY, JSON.stringify(next));
      setSession(next);
    } catch (caught) {
      setAuthError(caught instanceof Error ? caught.message : "Could not sign in.");
    } finally {
      setSigningIn(false);
    }
  }, []);

  /** An expired access token looks exactly like a wrong one; both mean sign in again. */
  const handle = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) {
        signOut();
        return;
      }
      setError(caught instanceof Error ? caught.message : "Something went wrong.");
    },
    [signOut],
  );

  // ── threads ────────────────────────────────────────────────────────────────
  const refreshThreads = useCallback(async () => {
    if (!session) return;
    try {
      setThreads((await api.listThreads(session.token)).items);
    } catch (caught) {
      handle(caught);
    }
  }, [session, handle]);

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);

  const reloadMessages = useCallback(
    async (id: string) => {
      if (!session) return;
      try {
        setMessages((await api.listMessages(session.token, id)).items);
      } catch (caught) {
        handle(caught);
      }
    },
    [session, handle],
  );

  const openThread = useCallback(
    async (id: string) => {
      abortRef.current?.();
      abortRef.current = null;
      setThreadId(id);
      setStreaming(null);
      setError(null);
      await reloadMessages(id);
    },
    [reloadMessages],
  );

  const newThread = useCallback(async () => {
    if (!session) return;
    try {
      const thread = await api.createThread(session.token);
      setThreads((current) => [thread, ...current]);
      setThreadId(thread.id);
      setMessages([]);
      setError(null);
    } catch (caught) {
      handle(caught);
    }
  }, [session, handle]);

  const removeThread = useCallback(
    async (id: string) => {
      if (!session) return;
      try {
        await api.deleteThread(session.token, id);
        setThreads((current) => current.filter((thread) => thread.id !== id));
        if (threadId === id) {
          setThreadId(null);
          setMessages([]);
        }
      } catch (caught) {
        handle(caught);
      }
    },
    [session, threadId, handle],
  );

  // ── streaming ──────────────────────────────────────────────────────────────
  const consume = useCallback(
    (token: string, assistantId: string, streamUrl: string, activeThreadId: string) => {
      setStreaming(assistantId);

      abortRef.current = streamReply(token, streamUrl, {
        onDelta: (text) =>
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + text, status: "streaming" }
                : message,
            ),
          ),
        onDone: () => {
          setStreaming(null);
          abortRef.current = null;
          // The stored rows are authoritative: they carry the final status and the model,
          // and the server may have named the thread while the reply was being written.
          void reloadMessages(activeThreadId);
          void refreshThreads();
        },
        onError: (message) => {
          setStreaming(null);
          abortRef.current = null;
          setError(message);
          setMessages((current) =>
            current.map((row) =>
              row.id === assistantId ? { ...row, status: "failed" } : row,
            ),
          );
        },
      });
    },
    [reloadMessages, refreshThreads],
  );

  const send = useCallback(
    async (content: string) => {
      if (!session) return;
      setError(null);

      try {
        let active = threadId;
        if (!active) {
          const thread = await api.createThread(session.token);
          setThreads((current) => [thread, ...current]);
          setThreadId(thread.id);
          active = thread.id;
        }

        const accepted = await api.send(session.token, active, content);
        const now = new Date().toISOString();

        // Both rows already exist on the server. This mirrors them locally so the screen
        // updates without waiting for a round trip.
        setMessages((current) => [
          ...current,
          {
            id: accepted.message_id,
            thread_id: active,
            seq: accepted.seq,
            role: "user",
            content,
            status: "complete",
            model: null,
            finish_reason: null,
            created_at: now,
          },
          {
            id: accepted.assistant_message_id,
            thread_id: active,
            seq: accepted.seq + 1,
            role: "assistant",
            content: "",
            status: "pending",
            model: null,
            finish_reason: null,
            created_at: now,
          },
        ]);

        consume(
          session.token,
          accepted.assistant_message_id,
          accepted.stream_url,
          active,
        );
      } catch (caught) {
        handle(caught);
      }
    },
    [session, threadId, consume, handle],
  );

  const stop = useCallback(async () => {
    if (!session || !threadId || !streaming) return;
    const target = streaming;
    try {
      abortRef.current?.();
      abortRef.current = null;
      await api.cancel(session.token, threadId, target);
    } catch (caught) {
      handle(caught);
    } finally {
      setStreaming(null);
      await reloadMessages(threadId);
    }
  }, [session, threadId, streaming, reloadMessages, handle]);

  const regenerate = useCallback(
    async (messageId: string) => {
      if (!session || !threadId) return;
      try {
        const accepted = await api.regenerate(session.token, threadId, messageId);
        await reloadMessages(threadId);
        consume(
          session.token,
          accepted.assistant_message_id,
          accepted.stream_url,
          threadId,
        );
      } catch (caught) {
        handle(caught);
      }
    },
    [session, threadId, consume, reloadMessages, handle],
  );

  const renameThread = useCallback(
    async (id: string, title: string) => {
      if (!session) return;
      try {
        const updated = await api.renameThread(session.token, id, title);
        setThreads((current) =>
          current.map((thread) => (thread.id === id ? updated : thread)),
        );
      } catch (caught) {
        handle(caught);
      }
    },
    [session, handle],
  );

  const summarise = useCallback(async () => {
    if (!session || !threadId) return;
    try {
      const result = await api.summarise(session.token, threadId);
      setError(
        result.summary
          ? "Earlier turns have been summarised."
          : "This conversation is too short to summarise.",
      );
    } catch (caught) {
      handle(caught);
    }
  }, [session, threadId, handle]);

  // ── render ─────────────────────────────────────────────────────────────────
  // The session lives in localStorage, which does not exist during server rendering, so
  // the first paint is deliberately neutral. It still shows the mark rather than nothing:
  // a blank page is indistinguishable from a broken one.
  if (restoring) {
    return (
      <div
        style={{
          display: "grid",
          placeItems: "center",
          minHeight: "100dvh",
          background: "var(--cream)",
        }}
      >
        <div className="splash-logo" style={{ width: 64, height: 64, fontSize: 30 }}>
          B
        </div>
        <span className="sr-only">Loading Bella</span>
      </div>
    );
  }

  // The splash sits in front of the gate, exactly as it did before — tap through the
  // greeting first, then register.
  if (!session) {
    return (
      <>
        {splash && <Splash onDismiss={dismissSplash} />}
        {!splash && <Gate onComplete={register} busy={signingIn} error={authError} />}
      </>
    );
  }

  return (
    <>
      {splash && <Splash onDismiss={dismissSplash} />}

      <div
        className={`sb-overlay ${mobileOpen ? "on" : ""}`}
        onClick={() => setMobileOpen(false)}
      />

      <div id="main" className="show">
        <Sidebar
          threads={threads}
          activeId={threadId}
          user={session.user}
          collapsed={collapsed}
          mobileOpen={mobileOpen}
          onToggleCollapse={() => setCollapsed((is) => !is)}
          onSelect={(id) => {
            setMobileOpen(false);
            void openThread(id);
          }}
          onCreate={newThread}
          onDelete={removeThread}
          onRename={renameThread}
          onSignOut={signOut}
        />

        <div id="chat-area">
          <Topbar
            user={session.user}
            onSearch={() => setSearching(true)}
            onSummarise={summarise}
            onAccount={signOut}
            onToggleSidebar={() => setMobileOpen((open) => !open)}
            canSummarise={messages.length > 4}
          />

          <MessageList
            messages={messages}
            streamingId={streaming}
            userName={session.user.preferred_name}
            onRegenerate={regenerate}
            onCopy={(content) => void navigator.clipboard?.writeText(content)}
          />

          {error && (
            <div
              role="status"
              style={{
                padding: "6px 20px",
                fontSize: 11,
                color: "var(--tmid)",
                textAlign: "center",
              }}
            >
              {error}
            </div>
          )}

          <Composer
            onSend={send}
            onStop={stop}
            streaming={streaming !== null}
            dark={dark}
            onToggleTheme={toggleTheme}
          />
        </div>
      </div>

      {searching && session && (
        <SearchOverlay
          token={session.token}
          onClose={() => setSearching(false)}
          onOpen={(id) => {
            setSearching(false);
            void openThread(id);
          }}
        />
      )}
    </>
  );
}

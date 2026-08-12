"use client";

import { useEffect, useState } from "react";

import { api, type SearchHit } from "@/lib/client";

/**
 * Search across every conversation.
 *
 * The legacy version scanned substrings of whatever threads happened to be loaded in the
 * browser, so anything older than the twenty messages localStorage held was invisible.
 * This queries Postgres full-text search, which sees the whole history.
 *
 * Snippets arrive from `ts_headline` with `<b>` around the matched terms. They are split
 * on those tags and rebuilt as elements rather than injected as HTML — the markup is the
 * database's, but it still does not get to be HTML in this page.
 */
function Snippet({ html }: { html: string }) {
  const parts = html.split(/(<b>.*?<\/b>)/g);
  return (
    <>
      {parts.map((part, index) =>
        part.startsWith("<b>") ? (
          <mark
            key={index}
            style={{
              background: "var(--goldpale)",
              color: "var(--navy)",
              padding: "0 2px",
            }}
          >
            {part.replace(/<\/?b>/g, "")}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

export function SearchOverlay({
  token,
  onClose,
  onOpen,
}: {
  token: string;
  onClose: () => void;
  onOpen: (threadId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setHits([]);
      setSearched(false);
      return;
    }
    // Debounced: one request per pause, not one per keystroke.
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        setHits((await api.search(token, trimmed)).items);
        setSearched(true);
      } finally {
        setBusy(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [query, token]);

  return (
    <div
      className="user-menu-overlay on"
      onClick={onClose}
      style={{ background: "rgba(11,37,69,.35)" }}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        style={{
          position: "fixed",
          top: "12%",
          left: "50%",
          transform: "translateX(-50%)",
          width: "min(560px, 92vw)",
          background: "var(--white)",
          borderRadius: 16,
          boxShadow: "var(--shadow-lg)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          maxHeight: "70vh",
        }}
      >
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search all conversations…"
          aria-label="Search all conversations"
          style={{
            padding: "16px 18px",
            border: "none",
            borderBottom: "1px solid var(--border)",
            fontFamily: "var(--font-body), sans-serif",
            fontSize: 14,
            color: "var(--text)",
            background: "transparent",
            outline: "none",
          }}
        />

        <div style={{ overflowY: "auto", padding: 8 }}>
          {busy && (
            <p style={{ padding: 14, fontSize: 12, color: "var(--tsoft)" }}>Searching…</p>
          )}

          {!busy && searched && hits.length === 0 && (
            <p style={{ padding: 14, fontSize: 12, color: "var(--tsoft)" }}>
              Nothing matched “{query.trim()}”.
            </p>
          )}

          {hits.map((hit) => (
            <div
              key={hit.message_id ?? hit.thread_id}
              className="thread-item"
              onClick={() => onOpen(hit.thread_id)}
            >
              <div className="thread-icon">💬</div>
              <div className="thread-info">
                <div className="thread-name">{hit.thread_title}</div>
                <div
                  className="thread-preview"
                  style={{ whiteSpace: "normal", fontSize: 11, lineHeight: 1.45 }}
                >
                  <Snippet html={hit.snippet} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

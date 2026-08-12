"use client";

import { useState } from "react";

import type { AuthenticatedUser, Thread } from "@/lib/client";

/**
 * The sidebar, ported from the legacy markup.
 *
 * "My Workshop" keeps all three zones — Activated, Bella 2.0, Bella 3.0. The Activated
 * items are the ones the legacy app really had; here they are marked `SOON` and disabled
 * until Phase 5 lands their backends, because a control that does nothing when pressed is
 * worse than one that says it is not ready.
 */

interface Feature {
  icon: string;
  label: string;
  badge: string;
  /** Why it is not usable yet — shown on hover. */
  pending?: string;
}

const ACTIVATED: Feature[] = [
  {
    icon: "🎙️",
    label: "Voice Recording",
    badge: "SOON",
    pending: "Voice arrives with Phase 5",
  },
  {
    icon: "📹",
    label: "Video Recording",
    badge: "SOON",
    pending: "Recording arrives with Phase 5",
  },
  {
    icon: "🔍",
    label: "Web Search",
    badge: "SOON",
    pending: "Tools arrive with Phase 5",
  },
  {
    icon: "⇄",
    label: "Converter",
    badge: "SOON",
    pending: "Converter arrives with Phase 5",
  },
];

const BELLA_2: Feature[] = [
  { icon: "📧", label: "Gmail", badge: "2.0" },
  { icon: "📁", label: "Google Drive", badge: "2.0" },
  { icon: "📅", label: "Calendar", badge: "2.0" },
  { icon: "🗺️", label: "Maps & Nav", badge: "2.0" },
  { icon: "🤝", label: "Google Meet", badge: "2.0" },
  { icon: "📊", label: "Charts & Graphs", badge: "2.0" },
  { icon: "📄", label: "File Downloads", badge: "2.0" },
  { icon: "📝", label: "Doc Edit & Convert", badge: "2.0" },
  { icon: "🖼️", label: "Image Transform", badge: "2.0" },
  { icon: "🔔", label: "Proactive AI", badge: "2.0" },
  { icon: "🎤", label: "Voice ID", badge: "2.0" },
  { icon: "🏠", label: "Smart Home", badge: "2.0" },
];

const BELLA_3: Feature[] = [{ icon: "✨", label: "Holographic Bella", badge: "3.0" }];

function FeatureGrid({ items }: { items: Feature[] }) {
  return (
    <div className="feat-grid">
      {items.map((feature) => (
        <div
          key={feature.label}
          className="feat-item"
          title={feature.pending ?? "Coming soon"}
        >
          <span className="feat-icon">{feature.icon}</span>
          <span className="feat-label">{feature.label}</span>
          <span className="feat-soon">{feature.badge}</span>
        </div>
      ))}
    </div>
  );
}

function initials(name: string | null): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0]?.toUpperCase() ?? "");
}

export function Sidebar({
  threads,
  activeId,
  user,
  collapsed,
  mobileOpen,
  onToggleCollapse,
  onSelect,
  onCreate,
  onDelete,
  onRename,
  onSignOut,
}: {
  threads: Thread[];
  activeId: string | null;
  user: AuthenticatedUser;
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapse: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onSignOut: () => void;
}) {
  const [shutterOpen, setShutterOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <div
      id="sidebar"
      className={`${collapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`}
    >
      <div className="sb-header">
        <div className="sb-logo" title="Bella.ai">
          B
        </div>
        <div className="sb-title">Bella.ai</div>
        <div className="sb-spacer" />
        <button
          id="sb-toggle-btn"
          onClick={onToggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--tmid)"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      </div>

      <div className="sb-user">
        <div className="sb-user-av" id="sb-av">
          {initials(user.preferred_name)}
        </div>
        <div className="sb-user-info">
          <div className="sb-user-name">{user.preferred_name ?? "Welcome"}</div>
          <div className="sb-user-status">
            <div className="sb-dot" />
            <span>Online</span>
          </div>
          <div id="not-you-btn" onClick={onSignOut} style={{ display: "block" }}>
            Not you?
          </div>
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: 4,
            flexShrink: 0,
          }}
        >
          <div className="mem-badge" title="Long-term memory arrives with Phase 4">
            💾
          </div>
        </div>
      </div>

      <button className="sb-new-chat" onClick={onCreate}>
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--gold)"
          strokeWidth="2.5"
          strokeLinecap="round"
        >
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        New Conversation
      </button>

      <div className="sb-section">Conversations</div>
      <div className="sb-threads" id="thread-list">
        {threads.map((thread) => (
          <div
            key={thread.id}
            className={`thread-item ${thread.id === activeId ? "active" : ""}`}
            onClick={() => renaming !== thread.id && onSelect(thread.id)}
          >
            <div className="thread-icon t-icon">💬</div>
            <div className="thread-info t-info">
              {renaming === thread.id ? (
                <input
                  className="thread-rename"
                  style={{ display: "block" }}
                  value={draft}
                  autoFocus
                  onChange={(event) => setDraft(event.target.value)}
                  onClick={(event) => event.stopPropagation()}
                  onBlur={() => setRenaming(null)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && draft.trim()) {
                      onRename(thread.id, draft.trim());
                      setRenaming(null);
                    }
                    if (event.key === "Escape") setRenaming(null);
                  }}
                />
              ) : (
                <>
                  <div className="thread-name">{thread.title}</div>
                  <div className="thread-preview">
                    {thread.message_count > 0
                      ? `${thread.message_count} messages`
                      : "No messages yet"}
                  </div>
                </>
              )}
            </div>
            <div className="thread-actions t-actions">
              <button
                className="thread-btn"
                title="Rename"
                onClick={(event) => {
                  event.stopPropagation();
                  setDraft(thread.title);
                  setRenaming(thread.id);
                }}
              >
                ✎
              </button>
              <button
                className="thread-btn"
                title="Delete"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(thread.id);
                }}
              >
                🗑
              </button>
            </div>
          </div>
        ))}
      </div>

      <button
        className={`sb-shutter-btn ${shutterOpen ? "open" : ""}`}
        onClick={() => setShutterOpen((open) => !open)}
      >
        <span id="shutter-label">My Workshop</span>
        <svg
          id="shutter-arrow"
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      <div className={`sb-shutter ${shutterOpen ? "open" : ""}`}>
        <div
          style={{
            fontSize: 8,
            fontWeight: 700,
            letterSpacing: 1,
            color: "var(--gold)",
            padding: "6px 4px 4px",
          }}
        >
          ✦ Activated
        </div>
        <FeatureGrid items={ACTIVATED} />

        <div
          style={{
            fontSize: 8,
            fontWeight: 700,
            letterSpacing: 1,
            color: "rgba(212,175,55,0.5)",
            padding: "8px 4px 4px",
            borderTop: "1px solid rgba(212,175,55,0.15)",
            marginTop: 4,
          }}
        >
          Bella 2.0 · Coming Soon
        </div>
        <FeatureGrid items={BELLA_2} />

        <div
          style={{
            fontSize: 8,
            fontWeight: 700,
            letterSpacing: 1,
            color: "rgba(212,175,55,0.3)",
            padding: "8px 4px 4px",
            borderTop: "1px solid rgba(212,175,55,0.15)",
            marginTop: 4,
          }}
        >
          Bella 3.0 · Coming Soon
        </div>
        <FeatureGrid items={BELLA_3} />
      </div>
    </div>
  );
}

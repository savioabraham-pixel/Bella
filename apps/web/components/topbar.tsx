"use client";

import { useEffect, useState } from "react";

import type { AuthenticatedUser } from "@/lib/client";

/**
 * The topbar dashboard, ported from the legacy markup.
 *
 * The clock and date are computed here and are genuinely live — the legacy version did
 * the same, but derived its UTC offset by hand and got DST wrong twice a year. This uses
 * the user's IANA zone through `Intl`, which does not.
 *
 * Location, Food For Thought and Convert are drawn because the layout is theirs, and are
 * inert because their endpoints (`/tools/geocode`, `/tools/fx`) arrive in Phase 5.
 */
export function Topbar({
  user,
  onSearch,
  onSummarise,
  onAccount,
  onToggleSidebar,
  canSummarise,
}: {
  user: AuthenticatedUser;
  onSearch: () => void;
  onSummarise: () => void;
  onAccount: () => void;
  onToggleSidebar: () => void;
  canSummarise: boolean;
}) {
  const [now, setNow] = useState<Date | null>(null);
  const [clock24, setClock24] = useState(false);

  useEffect(() => {
    // Started in an effect rather than at first render: the server has no clock the
    // browser agrees with, and a mismatch is a hydration error.
    setNow(new Date());
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const zone = user.timezone || "Asia/Kolkata";
  const date = now
    ? new Intl.DateTimeFormat("en-GB", {
        weekday: "short",
        day: "numeric",
        month: "short",
        timeZone: zone,
      }).format(now)
    : "—";
  const time = now
    ? new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: !clock24,
        timeZone: zone,
      }).format(now)
    : "—";

  return (
    <div className="chat-topbar" id="chat-topbar">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 14px",
          borderRight: "1px solid var(--border)",
          height: "100%",
          flexShrink: 0,
          width: "var(--tb-col)",
        }}
      >
        <button
          className="sb-toggle"
          onClick={onToggleSidebar}
          title="Menu"
          aria-label="Menu"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--tmid)"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <div style={{ minWidth: 0, overflow: "hidden" }}>
          <div id="topbar-welcome">Welcome {user.preferred_name ?? ""}</div>
          <div id="topbar-userid">{user.display_id}</div>
        </div>
      </div>

      <div className="tb-cell" title="Location arrives with Phase 5">
        <div className="tb-primary" style={{ opacity: 0.45 }}>
          📍 Location
        </div>
      </div>

      <div className="tb-cell">
        <div className="tb-primary">📅 {date}</div>
        <div style={{ display: "flex", alignItems: "baseline", marginTop: 2 }}>
          <span
            className="tb-clock-icon"
            onClick={() => setClock24((is24) => !is24)}
            title="Toggle 12/24hr"
          >
            🕐
          </span>
          <span className="tb-secondary" style={{ marginLeft: 4 }}>
            {time}
          </span>
          <span className="tb-tz-label">{zone.split("/").pop()}</span>
        </div>
      </div>

      <div className="tb-cell-expand" title="Food For Thought arrives with Phase 5">
        <div className="tb-thought-title">Bella&apos;s Food For Thought</div>
        <div className="tb-thought-text" style={{ opacity: 0.45 }}>
          &ldquo;Coming soon.&rdquo;
        </div>
      </div>

      <div
        className="tb-conv-btn"
        style={{ opacity: 0.45, cursor: "default" }}
        title="Converter arrives with Phase 5"
      >
        <div className="tb-primary">⇄ Convert</div>
        <div className="tb-secondary">Currency · Units · Time</div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          padding: "0 16px",
          flexShrink: 0,
          height: "100%",
          borderLeft: "1px solid var(--border)",
          width: "var(--tb-col)",
        }}
      >
        <button
          className="topbar-btn"
          onClick={onSearch}
          title="Search conversations"
          aria-label="Search conversations"
          style={{ background: "#0B2545", border: "1px solid rgba(212,175,55,0.3)" }}
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#D4AF37"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </button>

        {canSummarise && (
          <button
            className="topbar-btn"
            onClick={onSummarise}
            title="Summarise this conversation"
            aria-label="Summarise this conversation"
            style={{ background: "#0B2545", border: "1px solid rgba(212,175,55,0.3)" }}
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#D4AF37"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="21" y1="10" x2="3" y2="10" />
              <line x1="21" y1="6" x2="3" y2="6" />
              <line x1="21" y1="14" x2="3" y2="14" />
              <line x1="21" y1="18" x2="7" y2="18" />
            </svg>
          </button>
        )}

        <button
          className="topbar-btn"
          onClick={onAccount}
          title="My Account"
          aria-label="My Account"
          style={{ background: "#0B2545", border: "1px solid rgba(212,175,55,0.3)" }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#D4AF37"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </button>
      </div>
    </div>
  );
}

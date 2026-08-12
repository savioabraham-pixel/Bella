"use client";

import { useRef, useState } from "react";

/**
 * The input area, ported from the legacy markup.
 *
 * Attach, emoji, record and the voice toggle are drawn because the row is theirs and the
 * layout reads wrong without them. They are disabled, with a title saying why, until
 * Phase 5 brings `/files`, `/voice` and `/tools`. A control that silently does nothing is
 * a worse lie than one that admits it is not ready.
 */
const PENDING_FILES = "Attachments arrive with Phase 5";
const PENDING_VOICE = "Voice arrives with Phase 5";

export function Composer({
  onSend,
  onStop,
  streaming,
  dark,
  onToggleTheme,
}: {
  onSend: (content: string) => void;
  onStop: () => void;
  streaming: boolean;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const [value, setValue] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);

  function grow() {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || streaming) return;
    onSend(trimmed);
    setValue("");
    requestAnimationFrame(grow);
  }

  return (
    <div className="inp-area">
      <div id="fbar" />

      <div className="inp-box">
        <div className="inp-row">
          <textarea
            id="ti"
            ref={textarea}
            rows={1}
            value={value}
            placeholder="Ask Bella anything…"
            aria-label="Message Bella"
            onChange={(event) => {
              setValue(event.target.value);
              grow();
            }}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter is a newline — as it was.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />

          <div className="inp-actions">
            <button
              className="ibtn"
              disabled
              title={PENDING_FILES}
              aria-label="Attach file"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>

            <button
              className="ibtn"
              disabled
              title="Emoji & GIFs arrive with Phase 5"
              aria-label="Emoji"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M8 14s1.5 2 4 2 4-2 4-2" />
                <line x1="9" y1="9" x2="9.01" y2="9" />
                <line x1="15" y1="9" x2="15.01" y2="9" />
              </svg>
            </button>

            <button className="ibtn" disabled title={PENDING_VOICE} aria-label="Record">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <circle cx="12" cy="12" r="6" />
                <circle cx="12" cy="12" r="10" strokeDasharray="2 2" />
              </svg>
            </button>

            <button
              className="ibtn"
              disabled
              title={PENDING_VOICE}
              aria-label="Tap to speak"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <rect x="9" y="2" width="6" height="11" rx="3" />
                <path d="M5 10a7 7 0 0014 0" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            </button>

            {streaming ? (
              <button className="sbtn" onClick={onStop} title="Stop" aria-label="Stop">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="var(--gold)">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                className="sbtn"
                onClick={submit}
                disabled={!value.trim()}
                title="Send"
                aria-label="Send"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="var(--gold)"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                >
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}
          </div>
        </div>

        <div className="inp-footer">
          <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <span style={{ fontSize: 9, color: "#0B2545", fontWeight: 500 }}>Light</span>
            <div
              onClick={onToggleTheme}
              id="theme-btn"
              role="switch"
              aria-checked={dark}
              aria-label="Light or dark theme"
              title="Light / Dark"
              style={{
                width: 28,
                height: 16,
                borderRadius: 10,
                background: "#0B2545",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                padding: "0 2px",
              }}
            >
              <div
                id="theme-knob"
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: "50%",
                  background: "var(--gold)",
                  transition: "transform .2s",
                  transform: dark ? "translateX(10px)" : "translateX(0)",
                }}
              />
            </div>
            <span style={{ fontSize: 9, color: "#0B2545", fontWeight: 500 }}>Dark</span>
          </div>

          <div className="disc" style={{ flex: 1, textAlign: "center" }}>
            * Bella may make mistakes — Always Confirm important actions. Conversations
            stored securely to your account.
          </div>

          <div className="voice-toggle" title={PENDING_VOICE} style={{ opacity: 0.45 }}>
            <span style={{ color: "#0B2545", fontWeight: 500 }}>Text</span>
            <div className="toggle-sw" />
            <span style={{ color: "#0B2545", fontWeight: 500 }}>Voice</span>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

import type { Message } from "@/lib/client";

/**
 * The message list, ported from the legacy `#msgs` block.
 *
 * One deliberate difference from the original: replies render through a sanitising
 * markdown pipeline instead of `innerHTML`. The legacy client wrote model output straight
 * into the DOM as HTML — finding C4 — which turned anything the model could be persuaded
 * to emit into script running inside a page holding auth state. The bubbles look the same;
 * what reaches them cannot execute.
 */
function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-bella">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

function initials(name: string | null): string {
  if (!name) return "U";
  return (name.trim()[0] ?? "U").toUpperCase();
}

export function MessageList({
  messages,
  streamingId,
  userName,
  onRegenerate,
  onCopy,
}: {
  messages: Message[];
  streamingId: string | null;
  userName: string | null;
  onRegenerate: (messageId: string) => void;
  onCopy: (content: string) => void;
}) {
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div id="msgs">
        <div className="empty-state">
          <div className="empty-logo">B</div>
          <div className="empty-text">
            Good to see you{userName ? `, ${userName}` : ""}.
            <br />
            What would you like to talk about?
          </div>
        </div>
      </div>
    );
  }

  return (
    <div id="msgs">
      {messages.map((message) => {
        // A superseded attempt stays in the transcript but is not what the reader is
        // looking at; showing an empty one as a live reply would be misleading.
        if (
          message.role === "assistant" &&
          message.status === "cancelled" &&
          !message.content
        ) {
          return null;
        }

        const isUser = message.role === "user";
        const isStreaming = message.id === streamingId;
        const thinking = !isUser && !message.content && message.status !== "failed";
        const canRetry =
          !isUser &&
          !isStreaming &&
          ["complete", "failed", "cancelled"].includes(message.status);

        return (
          <div key={message.id} className={`msg ${isUser ? "user" : "bella"}`}>
            <div className={`mav ${isUser ? "u" : "b"}`}>
              {isUser ? initials(userName) : "B"}
            </div>

            <div className="msg-wrap">
              {thinking ? (
                <div className="thinking-indicator">
                  <div className="thinking-dots">
                    <div className="tdot" />
                    <div className="tdot" />
                    <div className="tdot" />
                  </div>
                  Bella is thinking…
                </div>
              ) : (
                <div className="mbub">
                  {message.status === "failed" && !message.content ? (
                    <span style={{ color: "var(--danger)" }}>
                      That reply did not finish. Your message is saved — try again.
                    </span>
                  ) : isUser ? (
                    <span style={{ whiteSpace: "pre-wrap" }}>{message.content}</span>
                  ) : (
                    <Markdown>{message.content}</Markdown>
                  )}
                </div>
              )}

              {!thinking && (
                <div className={`msg-actions ${isUser ? "user-actions" : ""}`}>
                  <button className="msg-act-btn" onClick={() => onCopy(message.content)}>
                    ⧉ Copy
                  </button>
                  {canRetry && (
                    <button
                      className="msg-act-btn"
                      onClick={() => onRegenerate(message.id)}
                    >
                      ↻ Try again
                    </button>
                  )}
                  {message.status === "cancelled" && message.content && (
                    <span style={{ fontSize: 10, color: "var(--tsoft)" }}>Stopped</span>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
      <div ref={bottom} />
    </div>
  );
}

"use client";

/**
 * Last-resort boundary.
 *
 * Replaces the root layout, so it cannot rely on anything the app defines — no design
 * tokens, no fonts, no stylesheet. Inline styles only.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", lineHeight: 1.6 }}
      >
        <h1 style={{ fontSize: "1.4rem", marginBottom: "0.5rem" }}>
          Bella failed to start
        </h1>
        <pre
          style={{
            background: "#f4f4f5",
            padding: "1rem",
            borderRadius: "0.5rem",
            overflow: "auto",
            fontSize: "0.8rem",
            whiteSpace: "pre-wrap",
          }}
        >
          {error.message}
          {error.digest ? `\n\ndigest: ${error.digest}` : ""}
        </pre>
        <button onClick={reset} style={{ marginTop: "1rem", padding: "0.5rem 1rem" }}>
          Try again
        </button>
      </body>
    </html>
  );
}

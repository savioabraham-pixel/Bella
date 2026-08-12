"use client";

import { useEffect } from "react";

/**
 * Error boundary for the app segment.
 *
 * A React tree that throws during render unmounts itself, and the result is a blank page —
 * indistinguishable from a server that never answered. This puts the actual failure on
 * screen instead, because "nothing happened" is the least debuggable bug report there is.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Bella failed to render:", error);
  }, [error]);

  return (
    <main className="grid min-h-dvh place-items-center bg-[var(--surface)] p-6">
      <div className="w-full max-w-lg rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-6">
        <h1 className="mb-2 font-[family-name:var(--font-display)] text-2xl text-[var(--text-primary)]">
          Something broke
        </h1>
        <p className="mb-4 text-sm text-[var(--text-secondary)]">
          The page failed to render. The details below are what to report.
        </p>

        <pre className="mb-4 max-h-64 overflow-auto rounded-lg bg-[var(--surface-sunken)] p-3 text-xs whitespace-pre-wrap text-[var(--text-primary)]">
          {error.message}
          {error.digest ? `\n\ndigest: ${error.digest}` : ""}
          {error.stack ? `\n\n${error.stack}` : ""}
        </pre>

        <div className="flex gap-2">
          <button
            onClick={reset}
            className="rounded-lg bg-[var(--color-navy)] px-4 py-2 text-sm font-medium text-[var(--color-gold-pale)]"
          >
            Try again
          </button>
          <button
            onClick={() => {
              localStorage.removeItem("bella.session");
              location.reload();
            }}
            className="rounded-lg border border-[var(--border-subtle)] px-4 py-2 text-sm font-medium"
          >
            Clear session and reload
          </button>
        </div>
      </div>
    </main>
  );
}

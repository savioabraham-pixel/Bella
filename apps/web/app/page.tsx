import { getApiHealth } from "@/lib/api";

/**
 * Phase 1 placeholder. Its only job is to prove the wiring end to end: the web container
 * reaches the API container over the internal network and renders what it got back.
 *
 * The chat shell replaces this in Phase 6.
 */
export default async function HomePage() {
  const health = await getApiHealth();

  return (
    <main className="mx-auto flex min-h-dvh max-w-2xl flex-col justify-center gap-8 p-8">
      <header className="flex items-center gap-4">
        <div
          aria-hidden
          className="grid size-14 place-items-center rounded-2xl bg-[var(--color-navy)] font-[family-name:var(--font-display)] text-3xl text-[var(--color-gold)]"
        >
          B
        </div>
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">Bella</h1>
          <p className="text-sm text-[var(--text-muted)]">
            Platform foundation · Phase 1
          </p>
        </div>
      </header>

      <section
        aria-labelledby="status-heading"
        className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-6"
      >
        <h2
          id="status-heading"
          className="mb-4 text-sm font-semibold tracking-wide uppercase"
        >
          API connection
        </h2>

        {health.ok ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-[var(--text-muted)]">Status</dt>
            <dd className="text-[var(--accent)]">connected</dd>
            <dt className="text-[var(--text-muted)]">Service</dt>
            <dd>{health.data.service}</dd>
            <dt className="text-[var(--text-muted)]">Version</dt>
            <dd>{health.data.version}</dd>
            <dt className="text-[var(--text-muted)]">Environment</dt>
            <dd>{health.data.environment}</dd>
            <dt className="text-[var(--text-muted)]">Uptime</dt>
            <dd>{health.data.uptime_seconds.toFixed(1)}s</dd>
          </dl>
        ) : (
          <p role="status" className="text-sm text-[var(--text-secondary)]">
            The API is not reachable ({health.error}). Check that the{" "}
            <code className="rounded bg-[var(--surface-sunken)] px-1.5 py-0.5">api</code>{" "}
            container is running.
          </p>
        )}
      </section>
    </main>
  );
}

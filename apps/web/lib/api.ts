/**
 * Server-side API client.
 *
 * This is a hand-written stub for Phase 1. From Phase 3 the typed client is generated
 * from the API's OpenAPI schema into `packages/api-client` and this file disappears —
 * a hand-maintained client is how request and response types drift apart.
 */

const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://api:8000";
const DEFAULT_TIMEOUT_MS = 5_000;

export interface ApiHealth {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  uptime_seconds: number;
}

export type Result<T> = { ok: true; data: T } | { ok: false; error: string };

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<Result<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_INTERNAL_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: "application/json", ...init.headers },
      cache: "no-store",
    });

    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    const reason =
      error instanceof Error && error.name === "AbortError"
        ? "timeout"
        : error instanceof Error
          ? error.message
          : "unknown error";
    return { ok: false, error: reason };
  } finally {
    clearTimeout(timer);
  }
}

export function getApiHealth(): Promise<Result<ApiHealth>> {
  return apiFetch<ApiHealth>("/health");
}

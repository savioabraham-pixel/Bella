import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiHealth } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("getApiHealth", () => {
  it("returns the payload when the API answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "ok",
            service: "bella-api",
            version: "0.1.0",
            environment: "local",
            uptime_seconds: 1.5,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    const result = await getApiHealth();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.service).toBe("bella-api");
    }
  });

  it("reports the status code rather than throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));

    const result = await getApiHealth();

    expect(result).toEqual({ ok: false, error: "HTTP 503" });
  });

  it("surfaces a network failure as a result, never an exception", async () => {
    // The page renders a status card either way; an unhandled rejection would blank it.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));

    const result = await getApiHealth();

    expect(result).toEqual({ ok: false, error: "ECONNREFUSED" });
  });

  it("reports an aborted request as a timeout", async () => {
    const abortError = new Error("aborted");
    abortError.name = "AbortError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    const result = await getApiHealth();

    expect(result).toEqual({ ok: false, error: "timeout" });
  });
});

import { NextResponse } from "next/server";

/**
 * Container healthcheck for the web service.
 *
 * Deliberately does not call the API: this answers "can this container serve pages?".
 * Coupling it to a dependency would let a slow API cause the orchestrator to restart a
 * perfectly healthy web container.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    { status: "ok", service: "bella-web", version: process.env.APP_VERSION ?? "0.1.0" },
    { headers: { "Cache-Control": "no-store" } },
  );
}

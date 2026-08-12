import { NextResponse, type NextRequest } from "next/server";

/**
 * Content Security Policy, issued per request with a nonce.
 *
 * This has to live in middleware rather than `next.config.ts` because a nonce is only
 * meaningful if it is fresh for every response, and a static header cannot be.
 *
 * The App Router streams its payload to the browser through inline `<script>` tags. A
 * policy of `script-src 'self'` blocks every one of them, React never receives the stream,
 * and the page hydrates into nothing — a blank screen with the failure visible only in the
 * console. The two ways out are `'unsafe-inline'`, which defeats the point of having a
 * policy, and a nonce. Next reads the nonce from this header and stamps it onto the
 * scripts it generates.
 *
 * `'strict-dynamic'` lets those nonced scripts load the chunks they need without every
 * chunk URL having to be enumerated here.
 */
const isDevelopment = process.env.NODE_ENV === "development";

export function middleware(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());

  const policy = [
    "default-src 'self'",
    // Next injects styles at runtime; there is no nonce path for those.
    "style-src 'self' 'unsafe-inline'",
    [
      "script-src 'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      // Webpack's dev runtime evaluates module code; production has no such need.
      isDevelopment ? "'unsafe-eval'" : "",
    ]
      .filter(Boolean)
      .join(" "),
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob:",
    "font-src 'self' data:",
    // The API is same-origin through the rewrite in next.config.ts. The Firebase origins
    // are for Phase 6's real sign-in; ws: is the dev server's hot reload channel.
    [
      "connect-src 'self'",
      process.env.NEXT_PUBLIC_API_URL ?? "",
      "https://*.googleapis.com",
      "https://*.firebaseio.com",
      "wss://*.firebaseio.com",
      isDevelopment ? "ws: http://localhost:* http://127.0.0.1:*" : "",
    ]
      .filter(Boolean)
      .join(" "),
    "frame-src 'self' https://*.firebaseapp.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    // Only in deployed environments. Local development is served over plain http on
    // loopback, and upgrading those requests points them at a port serving no TLS.
    isDevelopment ? "" : "upgrade-insecure-requests",
  ]
    .filter(Boolean)
    .join("; ");

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", policy);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set("content-security-policy", policy);
  return response;
}

export const config = {
  matcher: [
    // Everything except static assets, which are served with their own headers and do not
    // execute scripts.
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};

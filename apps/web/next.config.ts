import type { NextConfig } from "next";

/**
 * The CSP is NOT here.
 *
 * It is issued per request from `middleware.ts`, because it carries a nonce and a nonce
 * that is the same on every response is not a nonce. A static policy here would also be
 * merged with that one by the browser, and two policies are enforced as their
 * intersection — which is how a page ends up blocked by a rule nobody thinks is active.
 */
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=(self), interest-cohort=()",
  },
  // Deployed environments only. A browser ignores HSTS over plain http, but sending it
  // from a development server is still a claim this origin cannot honour.
  ...(process.env.NODE_ENV === "development"
    ? []
    : [
        {
          key: "Strict-Transport-Security",
          value: "max-age=63072000; includeSubDomains; preload",
        },
      ]),
];

const nextConfig: NextConfig = {
  // Emits a self-contained server, so the runtime image carries no package manager
  // and only the modules actually imported.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,

  typedRoutes: true,

  // Next's dev server refuses /_next/* requests whose Origin it does not recognise.
  // `localhost` and `127.0.0.1` are the same machine but not the same origin, and a
  // rejected chunk request leaves a page that hydrates into nothing.
  allowedDevOrigins: ["localhost", "127.0.0.1", "[::1]"],

  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },

  // In development the browser talks to /api/* on the same origin and Next proxies to the
  // API container. That keeps cookies first-party and removes CORS from the dev loop.
  async rewrites() {
    const apiUrl = process.env.API_INTERNAL_URL ?? "http://api:8000";
    return [{ source: "/api/v1/:path*", destination: `${apiUrl}/v1/:path*` }];
  },
};

export default nextConfig;

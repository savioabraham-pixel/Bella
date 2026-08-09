import type { NextConfig } from "next";

/**
 * The CSP here is the one that matters — it governs the rendered page.
 *
 * The legacy app shipped no CSP at all and loaded four un-pinned CDN scripts, which is
 * how a cdnjs compromise would have executed code in a page holding auth state. Nothing
 * is loaded from a third-party origin any more, so `script-src 'self'` is achievable.
 */
const contentSecurityPolicy = [
  "default-src 'self'",
  // 'unsafe-inline' for styles is required by Next's runtime style injection.
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self'" + (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""),
  "img-src 'self' data: blob: https:",
  "media-src 'self' blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${process.env.NEXT_PUBLIC_API_URL ?? ""} https://*.googleapis.com https://*.firebaseio.com wss://*.firebaseio.com`,
  "frame-src 'self' https://*.firebaseapp.com",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=(self), interest-cohort=()",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig: NextConfig = {
  // Emits a self-contained server, so the runtime image carries no package manager
  // and only the modules actually imported.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,

  experimental: {
    typedRoutes: true,
  },

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

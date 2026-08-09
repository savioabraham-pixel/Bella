# syntax=docker/dockerfile:1.11
#
# Bella web — Next.js 15.
#
#   base     pinned Node, corepack-managed pnpm
#   deps     node_modules only, cached on the lockfile
#   dev      hot reload, source bind-mounted by compose
#   builder  production compile → standalone output
#   prod     runtime only: node + .next/standalone, no pnpm, no sources
#
# Build context is apps/web.

ARG NODE_VERSION=22.21
ARG ALPINE_VERSION=3.22

# ─── base ─────────────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine${ALPINE_VERSION} AS base

# `apk upgrade` first: a pinned base tag is reproducible but frozen, so its OS packages
# drift behind Alpine's security updates between rebuilds. Trivy caught exactly this —
# Alpine 3.20 shipping openssl 3.3.2 with two criticals — and pinning alone would only
# defer it. Upgrading at build time keeps the pin *and* the patches.
#
# libc6-compat: some Next.js native deps expect glibc symbols on Alpine.
RUN apk upgrade --no-cache \
 && apk add --no-cache libc6-compat curl

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    NEXT_TELEMETRY_DISABLED=1

# The corepack bundled with node:22.11 ships an expired signing key and fails to
# verify pnpm at runtime; install a current one before enabling it.
RUN npm install --global corepack@latest \
 && corepack enable \
 && corepack prepare pnpm@9.15.0 --activate

WORKDIR /app

# ─── deps ─────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY package.json pnpm-lock.yaml* ./

RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    if [ -f pnpm-lock.yaml ]; then \
        pnpm install --frozen-lockfile; \
    else \
        pnpm install; \
    fi

# ─── dev ──────────────────────────────────────────────────────────────────────
FROM base AS dev

ENV NODE_ENV=development

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# node:alpine ships a `node` user at uid 1000.
#
# `.next` must exist in the image before compose mounts a named volume over it: Docker
# seeds a named volume from the image path, but creates it root-owned when that path is
# absent — which the unprivileged runtime user then cannot write to.
RUN mkdir -p /app/.next \
 && chown -R node:node /app
USER node

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=5 \
    CMD curl --fail --silent http://localhost:3000/api/health || exit 1

CMD ["pnpm", "dev"]

# ─── builder ──────────────────────────────────────────────────────────────────
FROM base AS builder

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NODE_ENV=production

RUN pnpm build

# ─── prod ─────────────────────────────────────────────────────────────────────
# Deliberately NOT `FROM base`: that stage carries corepack, pnpm and curl, none of which
# the runtime uses. Trivy found ~40 CRITICAL/HIGH advisories in the package manager's own
# dependency tree alone. npm is removed for the same reason — `node server.js` needs the
# runtime, not a way to install more of it.
FROM node:${NODE_VERSION}-alpine${ALPINE_VERSION} AS prod

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

RUN apk upgrade --no-cache \
 && apk add --no-cache curl \
 && rm -rf /usr/local/lib/node_modules/npm \
           /usr/local/bin/npm \
           /usr/local/bin/npx \
           /opt/yarn-* \
           /usr/local/bin/yarn \
           /usr/local/bin/yarnpkg

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

# `output: "standalone"` emits a self-contained server with only the modules actually
# imported — a fraction of node_modules, and no pnpm in the runtime image.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:3000/api/health || exit 1

CMD ["node", "server.js"]

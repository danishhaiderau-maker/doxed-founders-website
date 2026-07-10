FROM node:20-bookworm-slim AS build
WORKDIR /app

# OpenSSL is required by Prisma's query engine at runtime. node:20-bookworm-slim
# ships libssl-3 but Prisma's engine probes for openssl-1.1.x by default and
# warns/fails. Install openssl explicitly so the probe succeeds.
RUN apt-get update -y \
  && apt-get install -y --no-install-recommends openssl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Marker step — confirms the image pulls and a basic RUN executes.
RUN node --version && npm --version

# Copy lockfile + root manifests + the prune helper first for layer cache.
COPY package.json package-lock.json turbo.json tsconfig.base.json ./
COPY scripts/docker-prune-workspaces.mjs ./scripts/docker-prune-workspaces.mjs

# Prune the workspaces list so npm only sees apps/api + packages/* (the absent
# apps/web, apps/founder-node, apps/mobile-android would otherwise abort install).
RUN node scripts/docker-prune-workspaces.mjs

# Copy EVERY workspace's package.json BEFORE install so npm can resolve the
# inter-workspace symlinks (packages/founder-vault depends on packages/utils,
# apps/api depends on packages/*). Without these manifests present at install
# time, npm workspaces leaves the symlinks dangling and `tsc` in founder-vault
# fails with "Cannot find module '@dcf/utils'" on a fresh Linux image.
COPY packages/utils/package.json ./packages/utils/package.json
COPY packages/founder-vault/package.json ./packages/founder-vault/package.json
COPY packages/types/package.json ./packages/types/package.json
COPY packages/config/package.json ./packages/config/package.json
COPY packages/ui/package.json ./packages/ui/package.json
COPY apps/api/package.json ./apps/api/package.json

# Install with npm install (not ci). The committed lockfile is generated on
# Windows, so strict `npm ci` aborts on Linux due to platform-specific optional
# dependency drift (@img/sharp-*, utf-8-validate). npm install regenerates the
# lockfile in-place for the linux target. bcrypt@6 + sharp@0.34 ship prebuilt
# linux-x64 binaries, so no native toolchain is needed.
RUN npm install --no-audit --no-fund

# NOW copy the actual source. The node_modules symlinks already point at the
# right workspace dirs; source fills them in.
COPY apps/api ./apps/api
COPY packages ./packages
COPY prisma ./prisma
COPY scripts/start-api-prod.mjs scripts/prisma-run.mjs ./scripts/

RUN npm run build:utils
RUN npx prisma generate --schema=prisma/schema.prisma
RUN npm run build --workspace=@dcf/api
RUN node -e "console.log('build ok; dist/main exists:', require('fs').existsSync('./apps/api/dist/main.js'))"

# --- runtime stage (slim) ----------------------------------------------------
FROM node:20-bookworm-slim AS runtime
WORKDIR /app

# Prisma needs openssl in the runtime stage too (it's a fresh slim image, not
# the build stage's image with openssl already installed).
RUN apt-get update -y \
  && apt-get install -y --no-install-recommends openssl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production
ENV PRISMA_DB_PUSH=true

COPY --from=build /app /app

EXPOSE 4000
CMD ["node", "scripts/start-api-prod.mjs"]

FROM node:20-bookworm-slim AS build
WORKDIR /app

# Marker step — confirms the image pulls and a basic RUN executes. If the build
# fails before/at this point, the problem is image pull / build env, not our code.
RUN node --version && npm --version

# Copy lockfile + root manifests + the prune helper first for layer cache.
COPY package.json package-lock.json turbo.json tsconfig.base.json ./
COPY scripts/docker-prune-workspaces.mjs ./scripts/docker-prune-workspaces.mjs

# This image only contains apps/api, but the root package.json declares
# workspaces ["apps/*","packages/*"], so npm would look for apps/web,
# apps/founder-node, apps/mobile-android (not copied) and fail. The helper
# rewrites the workspaces list in package.json + the lockfile root entry AND
# deletes the orphaned lockfile keys (the absent apps/* targets plus the
# dangling node_modules/@dcf/{web,founder-node,mobile-android} link entries that
# point at them) so npm install doesn't abort with EMISSINGTARGET.
RUN node scripts/docker-prune-workspaces.mjs

# Install with npm install (not ci). The committed lockfile is generated on
# Windows, so strict `npm ci` aborts on Linux due to platform-specific optional
# dependency drift (@img/sharp-*, utf-8-validate). npm install regenerates the
# lockfile in-place for the linux target. bcrypt@6 + sharp@0.34 ship prebuilt
# linux-x64 binaries, so no native toolchain is needed.
RUN npm install --no-audit --no-fund

# Copy source for the API + package deps + prisma + launcher.
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

ENV NODE_ENV=production
ENV PRISMA_DB_PUSH=true

COPY --from=build /app /app

EXPOSE 4000
CMD ["node", "scripts/start-api-prod.mjs"]

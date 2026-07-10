FROM node:20-bookworm-slim AS build
WORKDIR /app

# Copy lockfile + root manifests first for cache. Source for workspaces is
# copied after the workspace list is narrowed (below) so npm only sees what
# the API-only image actually contains.
COPY package.json package-lock.json turbo.json tsconfig.base.json ./

# Restrict workspaces to what exists in this image. The root package.json
# declares ["apps/*","packages/*"], but only apps/api is copied here —
# apps/web, apps/founder-node and apps/mobile-android are absent, which makes
# `npm ci` fail. packages/founder-ide-extension is also excluded because it is
# not present in the committed lockfile and is irrelevant to the API. We
# rewrite the workspaces list in BOTH package.json and package-lock.json
# (npm ci checks that the two agree) before installing.
RUN node -e "\
const fs = require('fs');\
const ws = ['apps/api','packages/utils','packages/founder-vault','packages/types','packages/config','packages/ui'];\
const pkg = JSON.parse(fs.readFileSync('./package.json','utf8'));\
pkg.workspaces = ws;\
fs.writeFileSync('./package.json', JSON.stringify(pkg, null, 2));\
const lock = JSON.parse(fs.readFileSync('./package-lock.json','utf8'));\
lock.packages[''].workspaces = ws;\
fs.writeFileSync('./package-lock.json', JSON.stringify(lock, null, 2));"

RUN npm ci

# Now copy the actual source for everything we need to build.
COPY apps/api ./apps/api
COPY packages ./packages
COPY prisma ./prisma
COPY scripts/start-api-prod.mjs scripts/prisma-run.mjs ./scripts/

RUN npm run build:utils \
  && npx prisma generate --schema=prisma/schema.prisma \
  && npm run build --workspace=@dcf/api

# --- runtime stage ----------------------------------------------------------
# Slim runtime. bcrypt@6 ships prebuilt linux-x64 binaries via node-gyp-build
# and sharp@0.34 pulls @img/sharp-linux-x64, so no native toolchain is needed
# at runtime; node:20-bookworm-slim is sufficient.
FROM node:20-bookworm-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production
ENV PRISMA_DB_PUSH=true

COPY --from=build /app /app

EXPOSE 4000
CMD ["node", "scripts/start-api-prod.mjs"]

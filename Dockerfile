FROM node:20-bookworm-slim AS build
WORKDIR /app

# Native build tools. bcrypt@6 and sharp@0.34 ship prebuilt linux-x64 binaries,
# but a transitive native dep could fall back to source compilation, so install
# the toolchain defensively (kept in the build stage only; runtime stays slim).
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 make g++ \
  && rm -rf /var/lib/apt/lists/*

# Copy lockfile + root manifests first for layer cache.
COPY package.json package-lock.json turbo.json tsconfig.base.json ./

# This image only contains apps/api (apps/web, apps/founder-node, apps/mobile-android
# are NOT copied), but the root package.json declares workspaces ["apps/*","packages/*"].
# `npm ci` then fails because it cannot find the missing apps/* package.json files.
# Fix: rewrite the workspaces list in BOTH package.json and the lockfile's root
# entry (npm ci checks that the two agree) so only apps/api + packages/* are seen.
RUN node -e "const fs=require('fs');const ws=['apps/api','packages/*'];for(const f of ['./package.json','./package-lock.json']){const j=JSON.parse(fs.readFileSync(f,'utf8'));if(f.endsWith('-lock.json')){j.packages[''].workspaces=ws;}else{j.workspaces=ws;}fs.writeFileSync(f,JSON.stringify(j,null,2));}"

RUN npm ci

# Copy the actual source for the API + its package deps + prisma + launcher.
COPY apps/api ./apps/api
COPY packages ./packages
COPY prisma ./prisma
COPY scripts/start-api-prod.mjs scripts/prisma-run.mjs ./scripts/

RUN npm run build:utils \
  && npx prisma generate --schema=prisma/schema.prisma \
  && npm run build --workspace=@dcf/api

# --- runtime stage (slim, no toolchain) --------------------------------------
FROM node:20-bookworm-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production
ENV PRISMA_DB_PUSH=true

COPY --from=build /app /app

EXPOSE 4000
CMD ["node", "scripts/start-api-prod.mjs"]

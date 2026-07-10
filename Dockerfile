FROM node:20-bookworm-slim AS build
WORKDIR /app

# Marker step — confirms the image pulls and a basic RUN executes. If the build
# fails before/at this point, the problem is image pull or build env, not our code.
RUN node --version && npm --version

# Copy lockfile + root manifests first for layer cache.
COPY package.json package-lock.json turbo.json tsconfig.base.json ./

# This image only contains apps/api, but the root package.json declares
# workspaces ["apps/*","packages/*"], so npm would look for apps/web,
# apps/founder-node, apps/mobile-android (not copied) and fail. Rewrite the
# workspaces list in BOTH package.json and the lockfile root entry (npm checks
# they agree) so only apps/api + packages/* are seen.
RUN node -e "const fs=require('fs');const ws=['apps/api','packages/*'];for(const f of ['./package.json','./package-lock.json']){const j=JSON.parse(fs.readFileSync(f,'utf8'));if(f.endsWith('-lock.json')){j.packages[''].workspaces=ws;}else{j.workspaces=ws;}fs.writeFileSync(f,JSON.stringify(j,null,2));}" \
  && node -e "console.log('workspaces:',JSON.stringify(require('./package.json').workspaces))"

# Install with npm install (not ci). The lockfile is generated on Windows, so
# strict `npm ci` aborts on Linux due to platform-specific optional-dep drift
# (@img/sharp-*, utf-8-validate). npm install regenerates in-place for linux.
# bcrypt@6 + sharp@0.34 ship prebuilt linux-x64 binaries, so no toolchain needed.
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

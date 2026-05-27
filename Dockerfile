FROM node:20-bookworm-slim

WORKDIR /app

# Monorepo: install and build NestJS API only
COPY package.json package-lock.json turbo.json ./
COPY apps/api ./apps/api
COPY packages ./packages
COPY prisma ./prisma
COPY scripts/start-api-prod.mjs scripts/prisma-run.mjs ./scripts/

RUN npm ci \
  && npm run build:utils \
  && npx prisma generate --schema=prisma/schema.prisma \
  && npm run build --workspace=@dcf/api

ENV NODE_ENV=production
EXPOSE 4000

CMD ["node", "scripts/start-api-prod.mjs"]

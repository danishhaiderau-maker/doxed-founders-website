-- Phase 1 (kernel) — multi-instance routing cache (Layer 1 of Routing Engine v2).
-- See apps/api/src/routing-engine/routing-engine.cache.ts.
--
-- The original implementation used a per-process LRU Map, which does not
-- share state across Railway replicas. This table backs the optional
-- NeonRoutingCache when ROUTING_CACHE_BACKEND=neon.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS) so it is safe to apply on
-- databases that already had the table created via `prisma db push`.

CREATE TABLE IF NOT EXISTS "RoutingCacheEntry" (
    "cacheKey"   TEXT             NOT NULL,
    "decision"   JSONB            NOT NULL,
    "expiresAt"  TIMESTAMP(3)     NOT NULL,
    "createdAt"  TIMESTAMP(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"  TIMESTAMP(3)     NOT NULL,

    CONSTRAINT "RoutingCacheEntry_pkey" PRIMARY KEY ("cacheKey")
);

CREATE INDEX IF NOT EXISTS "RoutingCacheEntry_expiresAt_idx" ON "RoutingCacheEntry"("expiresAt");

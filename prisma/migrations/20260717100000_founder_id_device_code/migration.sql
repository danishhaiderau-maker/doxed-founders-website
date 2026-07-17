-- Founder OS Phase 2 — Founder ID + device-code (RFC 8628) + token lifecycle.
--
-- Adds the user-level "Founder ID" concept above the per-node `nodeId`.
-- A Founder ID is a revocable user identity issued by the Founder OS API;
-- a node is a device authorized by that Founder ID. The 1:1 mapping to
-- `userId` is captured at pair time and stored on the node row so we can
-- later decouple login identity from device-authorization identity without
-- a second migration.
--
-- Also adds the RFC 8628 device-authorization grant table so the Founder
-- Node tray can run a first-run "Sign in with Founder ID" flow (short
-- userCode + browser authorize) instead of the legacy paste-an-8-char-code
-- flow. The device polls /founder-node/device-code/poll with the long
-- deviceCode until the founder authorizes (or denies) in the browser.
--
-- And records per-install IPC identity (installId + bcrypt-hashed ipcSecret)
-- used by the Phase 3 named-pipe transport. The installId names the pipe
-- (\\.\pipe\founder-ide-{installId}); the secret is presented during the
-- handshake. Stored as a hash so a DB leak doesn't expose the live pipe
-- credential.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS) so it
-- is safe to apply on databases where the columns were already added via
-- `prisma db push` during development.

-- ─── FounderNode: founderId + token lifecycle + IPC identity ────────────────

ALTER TABLE "FounderNode"
    ADD COLUMN IF NOT EXISTS "founderId" TEXT;

ALTER TABLE "FounderNode"
    ADD COLUMN IF NOT EXISTS "tokenExpiresAt" TIMESTAMP(3);

ALTER TABLE "FounderNode"
    ADD COLUMN IF NOT EXISTS "tokenRotatedAt" TIMESTAMP(3);

ALTER TABLE "FounderNode"
    ADD COLUMN IF NOT EXISTS "installId" TEXT;

ALTER TABLE "FounderNode"
    ADD COLUMN IF NOT EXISTS "ipcSecretHash" TEXT;

-- Index founderId for the "list all nodes for this founder" query (today
-- equivalent to the userId index, but prepares for the founder/user split).
CREATE INDEX IF NOT EXISTS "FounderNode_founderId_idx" ON "FounderNode"("founderId");

-- Index installId so the FounderIdeAdapter can look up the IPC pipe path by
-- installId (the IDE extension sends its installId during the handshake).
CREATE INDEX IF NOT EXISTS "FounderNode_installId_idx" ON "FounderNode"("installId");

-- ─── FounderNodeDeviceCode (RFC 8628 device-authorization grant) ────────────

CREATE TABLE IF NOT EXISTS "FounderNodeDeviceCode" (
    "id"              TEXT NOT NULL,
    "userId"          TEXT NOT NULL,
    "deviceCode"      TEXT NOT NULL,
    "userCode"        TEXT NOT NULL,
    "verificationUri" TEXT NOT NULL,
    "expiresAt"       TIMESTAMP(3) NOT NULL,
    "interval"        INTEGER NOT NULL DEFAULT 5,
    "status"          TEXT NOT NULL DEFAULT 'pending',
    "nodeToken"       TEXT,
    "nodeId"          TEXT,
    "founderId"       TEXT,
    "installId"       TEXT,
    "tokenExpiresAt"  TIMESTAMP(3),
    "createdAt"       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"       TIMESTAMP(3) NOT NULL,

    CONSTRAINT "FounderNodeDeviceCode_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX IF NOT EXISTS "FounderNodeDeviceCode_deviceCode_key"
    ON "FounderNodeDeviceCode"("deviceCode");

CREATE UNIQUE INDEX IF NOT EXISTS "FounderNodeDeviceCode_userCode_key"
    ON "FounderNodeDeviceCode"("userCode");

CREATE INDEX IF NOT EXISTS "FounderNodeDeviceCode_userId_idx"
    ON "FounderNodeDeviceCode"("userId");

CREATE INDEX IF NOT EXISTS "FounderNodeDeviceCode_expiresAt_idx"
    ON "FounderNodeDeviceCode"("expiresAt");

CREATE INDEX IF NOT EXISTS "FounderNodeDeviceCode_status_idx"
    ON "FounderNodeDeviceCode"("status");

ALTER TABLE "FounderNodeDeviceCode"
    DROP CONSTRAINT IF EXISTS "FounderNodeDeviceCode_userId_fkey";

ALTER TABLE "FounderNodeDeviceCode"
    ADD CONSTRAINT "FounderNodeDeviceCode_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

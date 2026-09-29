-- Additive only. Existing legacy requests are not silently assigned to a node.
-- Deployment requires a reviewed migration; no data is backfilled or deleted.
ALTER TABLE "FounderNode"
  ADD COLUMN "ideProvider" TEXT,
  ADD COLUMN "ideCapabilityVersion" INTEGER,
  ADD COLUMN "ideCapabilities" JSONB,
  ADD COLUMN "ideCapabilitiesAt" TIMESTAMP(3);

ALTER TABLE "PendingIdeDispatch"
  ADD COLUMN "targetNodeId" TEXT,
  ADD COLUMN "capabilityVersion" INTEGER,
  ADD COLUMN "targetCapability" TEXT,
  ADD COLUMN "envelopeHash" TEXT,
  ADD COLUMN "claimTokenHash" TEXT,
  ADD COLUMN "claimedAt" TIMESTAMP(3),
  ADD COLUMN "expiresAt" TIMESTAMP(3),
  ADD COLUMN "error" TEXT,
  ADD COLUMN "cancelRequestedAt" TIMESTAMP(3),
  ADD COLUMN "cancelReason" TEXT;

CREATE INDEX "PendingIdeDispatch_userId_targetNodeId_status_idx"
  ON "PendingIdeDispatch"("userId", "targetNodeId", "status");

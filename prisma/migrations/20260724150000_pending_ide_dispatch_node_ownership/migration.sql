-- Bind every new remote IDE action to the exact paired computer that
-- advertised the selected session. Existing unbound rows remain inert and
-- cannot be claimed through the node-scoped API.
ALTER TABLE "PendingIdeDispatch"
  ADD COLUMN "nodeId" TEXT,
  ADD COLUMN "claimedByNodeId" TEXT;

CREATE INDEX "PendingIdeDispatch_userId_nodeId_status_idx"
  ON "PendingIdeDispatch"("userId", "nodeId", "status");

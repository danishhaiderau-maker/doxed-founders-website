-- A close authority must be durably and atomically owned before any exchange
-- cancellation or reduce-only market close is submitted. A CLAIMED fence may
-- be recovered after its lease because no close request has started. Once the
-- fence reaches SUBMITTING, timeout alone can never authorize another submit:
-- recovery must reconcile the persisted target against authenticated exchange
-- position state. The immutable request id and event ledger preserve the full
-- transition history.
ALTER TABLE "SignalCycleParticipant"
  ADD COLUMN "terminalCloseClaimToken" TEXT,
  ADD COLUMN "terminalCloseClaimedAt" TIMESTAMP(3),
  ADD COLUMN "terminalCloseGeneration" TEXT,
  ADD COLUMN "terminalCloseAuthority" JSONB,
  ADD COLUMN "terminalClosePhase" TEXT,
  ADD COLUMN "terminalCloseRequestId" TEXT,
  ADD COLUMN "terminalCloseBeforeAmount" DECIMAL(18,8),
  ADD COLUMN "terminalCloseTargetAmount" DECIMAL(18,8),
  ADD COLUMN "terminalCloseQty" DECIMAL(18,8),
  ADD COLUMN "terminalCloseExchangeOrderId" BIGINT,
  ADD COLUMN "terminalCloseAcknowledgedAt" TIMESTAMP(3),
  ADD COLUMN "terminalCloseConfirmedAt" TIMESTAMP(3);

CREATE UNIQUE INDEX "SignalCycleParticipant_terminalCloseClaimToken_key"
  ON "SignalCycleParticipant"("terminalCloseClaimToken");

CREATE UNIQUE INDEX "SignalCycleParticipant_terminalCloseRequestId_key"
  ON "SignalCycleParticipant"("terminalCloseRequestId");

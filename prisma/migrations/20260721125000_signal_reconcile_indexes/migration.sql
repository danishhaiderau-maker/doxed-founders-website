-- Keep live relay reconciliation bounded as the research/event ledger grows.
CREATE INDEX IF NOT EXISTS "SignalCycleParticipant_userId_status_createdAt_idx"
  ON "SignalCycleParticipant"("userId", "status", "createdAt" DESC);

CREATE INDEX IF NOT EXISTS "SignalCycleEvent_participantId_eventType_createdAt_idx"
  ON "SignalCycleEvent"("participantId", "eventType", "createdAt" DESC);

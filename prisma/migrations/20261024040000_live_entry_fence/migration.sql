-- HM 24 Oct (#852) HM1: account entry fence for the flatten routes (additive only).
CREATE TABLE "LiveEntryFence" (
  "id" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "acquiredAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "LiveEntryFence_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "LiveEntryFence_userId_expiresAt_idx" ON "LiveEntryFence"("userId", "expiresAt");

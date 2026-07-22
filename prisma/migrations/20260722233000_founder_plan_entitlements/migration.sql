CREATE TYPE "FounderPlanTier" AS ENUM ('FREE', 'BUILDER', 'TEAM');
CREATE TYPE "FounderPlanStatus" AS ENUM ('ACTIVE', 'CANCELED', 'PAST_DUE');
CREATE TYPE "FounderTeamRole" AS ENUM ('OWNER', 'ADMIN', 'MEMBER');

CREATE TABLE "FounderPlanTeam" (
  "id" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "ownerUserId" TEXT NOT NULL,
  "weeklyWeightedUnitCap" INTEGER NOT NULL DEFAULT 20000000,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "FounderPlanTeam_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "FounderPlanSubscription" (
  "id" TEXT NOT NULL,
  "tier" "FounderPlanTier" NOT NULL,
  "status" "FounderPlanStatus" NOT NULL DEFAULT 'ACTIVE',
  "userId" TEXT,
  "teamId" TEXT,
  "currentPeriodStart" TIMESTAMP(3) NOT NULL,
  "currentPeriodEnd" TIMESTAMP(3) NOT NULL,
  "cancelAtPeriodEnd" BOOLEAN NOT NULL DEFAULT false,
  "stripeCustomerId" TEXT,
  "stripeSubscriptionId" TEXT,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "FounderPlanSubscription_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "FounderPlanSubscription_owner_check" CHECK (
    (("userId" IS NOT NULL)::int + ("teamId" IS NOT NULL)::int) = 1
  ),
  CONSTRAINT "FounderPlanSubscription_tier_owner_check" CHECK (
    ("tier" = 'BUILDER' AND "userId" IS NOT NULL)
    OR ("tier" = 'TEAM' AND "teamId" IS NOT NULL)
  )
);

CREATE TABLE "FounderPlanTeamMember" (
  "id" TEXT NOT NULL,
  "teamId" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "role" "FounderTeamRole" NOT NULL DEFAULT 'MEMBER',
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "FounderPlanTeamMember_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "FounderPlanSubscription_userId_key" ON "FounderPlanSubscription"("userId");
CREATE UNIQUE INDEX "FounderPlanSubscription_teamId_key" ON "FounderPlanSubscription"("teamId");
CREATE UNIQUE INDEX "FounderPlanSubscription_stripeSubscriptionId_key" ON "FounderPlanSubscription"("stripeSubscriptionId");
CREATE INDEX "FounderPlanSubscription_status_currentPeriodEnd_idx" ON "FounderPlanSubscription"("status", "currentPeriodEnd");
CREATE INDEX "FounderPlanTeam_ownerUserId_idx" ON "FounderPlanTeam"("ownerUserId");
CREATE UNIQUE INDEX "FounderPlanTeamMember_teamId_userId_key" ON "FounderPlanTeamMember"("teamId", "userId");
CREATE INDEX "FounderPlanTeamMember_userId_idx" ON "FounderPlanTeamMember"("userId");

ALTER TABLE "FounderPlanTeam"
ADD CONSTRAINT "FounderPlanTeam_ownerUserId_fkey"
FOREIGN KEY ("ownerUserId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "FounderPlanSubscription"
ADD CONSTRAINT "FounderPlanSubscription_userId_fkey"
FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "FounderPlanSubscription"
ADD CONSTRAINT "FounderPlanSubscription_teamId_fkey"
FOREIGN KEY ("teamId") REFERENCES "FounderPlanTeam"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "FounderPlanTeamMember"
ADD CONSTRAINT "FounderPlanTeamMember_teamId_fkey"
FOREIGN KEY ("teamId") REFERENCES "FounderPlanTeam"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "FounderPlanTeamMember"
ADD CONSTRAINT "FounderPlanTeamMember_userId_fkey"
FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "AiManagedReservation" ADD COLUMN "quotaOwnerKey" TEXT;
UPDATE "AiManagedReservation" SET "quotaOwnerKey" = 'user:' || "userId";
ALTER TABLE "AiManagedReservation" ALTER COLUMN "quotaOwnerKey" SET NOT NULL;
CREATE INDEX "AiManagedReservation_quotaOwnerKey_status_createdAt_idx"
ON "AiManagedReservation"("quotaOwnerKey", "status", "createdAt");

-- AlterEnum
DO $$ BEGIN
  ALTER TYPE "NotificationType" ADD VALUE 'WALL_MENTION';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- AlterTable User: coarse chat presence
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "lastSeenAt" TIMESTAMP(3);

-- AlterTable PlatformMessage: reply/quote
ALTER TABLE "PlatformMessage" ADD COLUMN IF NOT EXISTS "replyToId" TEXT;

-- AlterTable ProjectWallMessage: reply + soft-hide
ALTER TABLE "ProjectWallMessage" ADD COLUMN IF NOT EXISTS "replyToId" TEXT;
ALTER TABLE "ProjectWallMessage" ADD COLUMN IF NOT EXISTS "hiddenAt" TIMESTAMP(3);
ALTER TABLE "ProjectWallMessage" ADD COLUMN IF NOT EXISTS "hiddenById" TEXT;

-- CreateTable PlatformMessageReaction
CREATE TABLE IF NOT EXISTS "PlatformMessageReaction" (
    "id" TEXT NOT NULL,
    "messageId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "emoji" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "PlatformMessageReaction_pkey" PRIMARY KEY ("id")
);

-- CreateTable ChatThreadPreference
CREATE TABLE IF NOT EXISTS "ChatThreadPreference" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "targetId" TEXT NOT NULL,
    "pinned" BOOLEAN NOT NULL DEFAULT false,
    "muted" BOOLEAN NOT NULL DEFAULT false,
    "archived" BOOLEAN NOT NULL DEFAULT false,
    "pinnedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ChatThreadPreference_pkey" PRIMARY KEY ("id")
);

-- CreateTable ProjectWallReaction
CREATE TABLE IF NOT EXISTS "ProjectWallReaction" (
    "id" TEXT NOT NULL,
    "messageId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "emoji" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProjectWallReaction_pkey" PRIMARY KEY ("id")
);

-- CreateTable ProjectWallSettings
CREATE TABLE IF NOT EXISTS "ProjectWallSettings" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "postingMode" TEXT NOT NULL DEFAULT 'OPEN',
    "slowModeSeconds" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ProjectWallSettings_pkey" PRIMARY KEY ("id")
);

-- CreateTable ProjectWallMute
CREATE TABLE IF NOT EXISTS "ProjectWallMute" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "mutedById" TEXT NOT NULL,
    "reason" TEXT,
    "mutedUntil" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProjectWallMute_pkey" PRIMARY KEY ("id")
);

-- CreateTable ProjectWallReport
CREATE TABLE IF NOT EXISTS "ProjectWallReport" (
    "id" TEXT NOT NULL,
    "messageId" TEXT NOT NULL,
    "reporterId" TEXT NOT NULL,
    "reason" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'OPEN',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProjectWallReport_pkey" PRIMARY KEY ("id")
);

-- Indexes
CREATE INDEX IF NOT EXISTS "PlatformMessage_replyToId_idx" ON "PlatformMessage"("replyToId");
CREATE INDEX IF NOT EXISTS "PlatformMessageReaction_messageId_idx" ON "PlatformMessageReaction"("messageId");
CREATE INDEX IF NOT EXISTS "PlatformMessageReaction_userId_idx" ON "PlatformMessageReaction"("userId");
CREATE UNIQUE INDEX IF NOT EXISTS "PlatformMessageReaction_messageId_userId_emoji_key" ON "PlatformMessageReaction"("messageId", "userId", "emoji");

CREATE UNIQUE INDEX IF NOT EXISTS "ChatThreadPreference_userId_scope_targetId_key" ON "ChatThreadPreference"("userId", "scope", "targetId");
CREATE INDEX IF NOT EXISTS "ChatThreadPreference_userId_pinned_idx" ON "ChatThreadPreference"("userId", "pinned");
CREATE INDEX IF NOT EXISTS "ChatThreadPreference_userId_archived_idx" ON "ChatThreadPreference"("userId", "archived");

CREATE INDEX IF NOT EXISTS "ProjectWallMessage_replyToId_idx" ON "ProjectWallMessage"("replyToId");
CREATE INDEX IF NOT EXISTS "ProjectWallMessage_hiddenAt_idx" ON "ProjectWallMessage"("hiddenAt");

CREATE INDEX IF NOT EXISTS "ProjectWallReaction_messageId_idx" ON "ProjectWallReaction"("messageId");
CREATE INDEX IF NOT EXISTS "ProjectWallReaction_userId_idx" ON "ProjectWallReaction"("userId");
CREATE UNIQUE INDEX IF NOT EXISTS "ProjectWallReaction_messageId_userId_emoji_key" ON "ProjectWallReaction"("messageId", "userId", "emoji");

CREATE UNIQUE INDEX IF NOT EXISTS "ProjectWallSettings_projectId_key" ON "ProjectWallSettings"("projectId");

CREATE UNIQUE INDEX IF NOT EXISTS "ProjectWallMute_projectId_userId_key" ON "ProjectWallMute"("projectId", "userId");
CREATE INDEX IF NOT EXISTS "ProjectWallMute_userId_idx" ON "ProjectWallMute"("userId");
CREATE INDEX IF NOT EXISTS "ProjectWallMute_mutedUntil_idx" ON "ProjectWallMute"("mutedUntil");

CREATE UNIQUE INDEX IF NOT EXISTS "ProjectWallReport_messageId_reporterId_key" ON "ProjectWallReport"("messageId", "reporterId");
CREATE INDEX IF NOT EXISTS "ProjectWallReport_status_createdAt_idx" ON "ProjectWallReport"("status", "createdAt");
CREATE INDEX IF NOT EXISTS "ProjectWallReport_reporterId_idx" ON "ProjectWallReport"("reporterId");

-- Foreign keys (idempotent-ish: ignore if already present via DO blocks)
DO $$ BEGIN
  ALTER TABLE "PlatformMessage" ADD CONSTRAINT "PlatformMessage_replyToId_fkey" FOREIGN KEY ("replyToId") REFERENCES "PlatformMessage"("id") ON DELETE SET NULL ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "PlatformMessageReaction" ADD CONSTRAINT "PlatformMessageReaction_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "PlatformMessage"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "PlatformMessageReaction" ADD CONSTRAINT "PlatformMessageReaction_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ChatThreadPreference" ADD CONSTRAINT "ChatThreadPreference_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallMessage" ADD CONSTRAINT "ProjectWallMessage_replyToId_fkey" FOREIGN KEY ("replyToId") REFERENCES "ProjectWallMessage"("id") ON DELETE SET NULL ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallReaction" ADD CONSTRAINT "ProjectWallReaction_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "ProjectWallMessage"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallReaction" ADD CONSTRAINT "ProjectWallReaction_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallSettings" ADD CONSTRAINT "ProjectWallSettings_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallMute" ADD CONSTRAINT "ProjectWallMute_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallMute" ADD CONSTRAINT "ProjectWallMute_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallMute" ADD CONSTRAINT "ProjectWallMute_mutedById_fkey" FOREIGN KEY ("mutedById") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallReport" ADD CONSTRAINT "ProjectWallReport_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "ProjectWallMessage"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ProjectWallReport" ADD CONSTRAINT "ProjectWallReport_reporterId_fkey" FOREIGN KEY ("reporterId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

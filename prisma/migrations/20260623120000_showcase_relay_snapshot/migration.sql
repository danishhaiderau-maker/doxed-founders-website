-- AlterTable
ALTER TABLE "PlatformSettings" ADD COLUMN "showcaseRelaySnapshot" JSONB;
ALTER TABLE "PlatformSettings" ADD COLUMN "showcaseRelaySnapshotSeq" BIGINT;
ALTER TABLE "PlatformSettings" ADD COLUMN "showcaseRelaySnapshotAt" TIMESTAMP(3);

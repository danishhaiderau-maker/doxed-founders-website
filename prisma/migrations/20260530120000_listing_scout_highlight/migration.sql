-- AlterTable
ALTER TABLE "ListingApplication" ADD COLUMN IF NOT EXISTS "founderDoxxedStatus" TEXT DEFAULT 'DOXXED';
ALTER TABLE "ListingApplication" ADD COLUMN IF NOT EXISTS "scoutHighlightNote" TEXT;

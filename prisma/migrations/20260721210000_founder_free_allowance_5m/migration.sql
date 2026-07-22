ALTER TABLE "PlatformSettings"
ALTER COLUMN "founderPromoTokenCap" SET DEFAULT 5000000;

UPDATE "PlatformSettings"
SET "founderPromoTokenCap" = 5000000
WHERE "id" = 'default'
  AND "founderPromoTokenCap" IN (10000000, 30000000);

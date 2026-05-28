import { PrismaClient } from '@prisma/client';

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === 'development' ? ['error', 'warn'] : ['error'],
  });

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma;
}

const ADMIN_REVIEW_FIELDS = [
  'projectName',
  'ticker',
  'websiteUrl',
  'docsUrl',
  'whitepaperUrl',
  'contractAddress',
  'chainSlug',
  'dexscreenerUrl',
  'logoUrl',
  'telegramUrl',
  'founderName',
  'founderLinkedIn',
  'founderTwitter',
  'founderGithub',
  'founderVideoUrl',
  'founderInterviewUrl',
  'companyDetails',
  'auditUrl',
  'summary',
  'marketPreview',
] as const;

export function buildListingApplicationUpdates(body: Record<string, unknown>) {
  const data: Record<string, unknown> = {};

  for (const key of ADMIN_REVIEW_FIELDS) {
    if (!(key in body) || body[key] === undefined) {
      continue;
    }

    const value = body[key];
    if (key === 'marketPreview') {
      data.marketPreview = value;
      continue;
    }

    if (key === 'ticker' && typeof value === 'string') {
      data.ticker = value.toUpperCase();
      continue;
    }

    data[key] = value === '' ? null : value;
  }

  return data;
}

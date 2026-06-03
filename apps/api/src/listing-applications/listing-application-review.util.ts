import { ListingApplication, Prisma } from '@prisma/client';
import { ReviewListingApplicationDto } from './dto/listing-application.dto';

export const ADMIN_REVIEW_FIELD_KEYS = [
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
  'projectGithubUrl',
  'founderVideoUrl',
  'founderInterviewUrl',
  'companyDetails',
  'auditUrl',
  'summary',
  'marketPreview',
] as const satisfies readonly (keyof ListingApplication)[];

export type AdminReviewFieldKey = (typeof ADMIN_REVIEW_FIELD_KEYS)[number];

export function extractAdminReviewUpdates(
  dto: ReviewListingApplicationDto,
): Partial<Pick<ListingApplication, AdminReviewFieldKey>> {
  const updates: Partial<Pick<ListingApplication, AdminReviewFieldKey>> = {};

  for (const key of ADMIN_REVIEW_FIELD_KEYS) {
    if (!(key in dto) || dto[key as keyof ReviewListingApplicationDto] === undefined) {
      continue;
    }

    const value = dto[key as keyof ReviewListingApplicationDto];
    if (key === 'marketPreview') {
      updates.marketPreview = value as ListingApplication['marketPreview'];
      continue;
    }

    if (key === 'ticker' && typeof value === 'string') {
      updates.ticker = value.toUpperCase();
      continue;
    }

    updates[key] = (value === '' ? null : value) as never;
  }

  return updates;
}

export function toPrismaAdminUpdates(
  updates: Partial<Pick<ListingApplication, AdminReviewFieldKey>>,
): Prisma.ListingApplicationUpdateInput {
  const { marketPreview, ...rest } = updates;
  return {
    ...rest,
    ...(marketPreview !== undefined
      ? { marketPreview: marketPreview as Prisma.InputJsonValue }
      : {}),
  };
}

export function mergeListingApplication(
  application: ListingApplication,
  updates: Partial<Pick<ListingApplication, AdminReviewFieldKey>>,
): ListingApplication {
  return { ...application, ...updates };
}

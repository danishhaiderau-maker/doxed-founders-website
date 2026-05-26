export const FOUNDER_VERIFICATION_CRITERIA = [
  'FOUNDER_VIDEO',
  'PUBLIC_INTERVIEW',
  'LINKEDIN',
  'FOUNDER_NAME',
  'GITHUB',
  'COMPANY_DETAILS',
] as const;

export type FounderVerificationCriterion =
  (typeof FOUNDER_VERIFICATION_CRITERIA)[number];

export interface FounderVerificationInput {
  founderName?: string | null;
  founderLinkedIn?: string | null;
  founderGithub?: string | null;
  companyDetails?: string | null;
  founderVideoUrl?: string | null;
  founderInterviewUrl?: string | null;
}

export const FOUNDER_VERIFICATION_LABELS: Record<
  FounderVerificationCriterion,
  string
> = {
  FOUNDER_VIDEO: 'Founder video (on camera)',
  PUBLIC_INTERVIEW: 'Public interview / talk about the project',
  LINKEDIN: 'LinkedIn identity',
  FOUNDER_NAME: 'Founder full name',
  GITHUB: 'GitHub profile',
  COMPANY_DETAILS: 'Company / team details',
};

export function scoreFounderVerification(input: FounderVerificationInput) {
  const criteria: FounderVerificationCriterion[] = [];

  if (input.founderVideoUrl?.trim()) {
    criteria.push('FOUNDER_VIDEO');
  }
  if (input.founderInterviewUrl?.trim()) {
    criteria.push('PUBLIC_INTERVIEW');
  }
  if (input.founderLinkedIn?.trim()) {
    criteria.push('LINKEDIN');
  }
  if (input.founderName?.trim()) {
    criteria.push('FOUNDER_NAME');
  }
  if (input.founderGithub?.trim()) {
    criteria.push('GITHUB');
  }
  if (input.companyDetails?.trim()) {
    criteria.push('COMPANY_DETAILS');
  }

  const score = criteria.length;
  return {
    score,
    criteria,
    meetsThreshold: score >= 2,
  };
}

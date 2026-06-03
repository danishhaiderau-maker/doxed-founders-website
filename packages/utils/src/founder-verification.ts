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
  projectGithubUrl?: string | null;
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
  if (input.founderGithub?.trim() || input.projectGithubUrl?.trim()) {
    criteria.push('GITHUB');
  }
  if (input.companyDetails?.trim()) {
    criteria.push('COMPANY_DETAILS');
  }

  const score = criteria.length;
  const hasPublicAppearance = Boolean(
    input.founderVideoUrl?.trim() || input.founderInterviewUrl?.trim(),
  );

  return {
    score,
    criteria,
    hasPublicAppearance,
    /** Anyone can submit if they found a public founder video or interview. */
    meetsSubmissionThreshold: hasPublicAppearance,
    /** Admin publish prefers richer proof but video/interview alone is enough in beta. */
    meetsThreshold: hasPublicAppearance,
    meetsApprovalThreshold: score >= 2 || hasPublicAppearance,
  };
}

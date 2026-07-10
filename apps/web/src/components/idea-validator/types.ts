/**
 * Shared client types for the Founder Idea Validator (Phase 6).
 * Mirrors the backend IdeaValidationReport + IdeaCheck shapes so the
 * frontend is type-safe without importing server code.
 */

export type IdeaVerdict = 'novel' | 'empty' | 'moderate' | 'crowded';

export type IdeaCheckStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface CompetitorEntry {
  name: string;
  type: 'oss' | 'product' | 'startup' | string;
  url: string;
  description: string;
  stars?: number;
  traction?: string;
  funding?: string;
  differentiation?: string;
}

export interface OpenSourceReuseEntry {
  repo: string;
  license: string;
  whatToReuse: string;
  modulePath?: string;
  savedTimeEstimate?: string;
  lastPushedAt?: string;
  stars?: number;
}

export interface IdeaValidationReport {
  verdict: IdeaVerdict;
  summary: string;
  differentiation: string;
  differentiationScore: number;
  competitors: CompetitorEntry[];
  openSourceReuse: OpenSourceReuseEntry[];
}

/**
 * The IdeaCheck row as returned by the API. resultJson holds the full
 * IdeaValidationReport once status === COMPLETED.
 */
export interface IdeaCheck {
  id: string;
  userId: string;
  projectId?: string | null;
  applicationId?: string | null;
  ideaText: string;
  status: IdeaCheckStatus;
  searchQueries?: string[] | null;
  resultJson?: IdeaValidationReport | null;
  differentiationScore?: number | null;
  similarProjectsJson?: CompetitorEntry[] | null;
  suggestedOssJson?: OpenSourceReuseEntry[] | null;
  errorMessage?: string | null;
  dismissed: boolean;
  viewed: boolean;
  createdAt: string;
  completedAt?: string | null;
  updatedAt: string;
}

export const VERDICT_META: Record<IdeaVerdict, { label: string; color: string; emoji: string }> = {
  novel: { label: 'Novel', color: 'emerald', emoji: '🟢' },
  empty: { label: 'Under-explored', color: 'sky', emoji: '🔵' },
  moderate: { label: 'Moderate', color: 'amber', emoji: '🟡' },
  crowded: { label: 'Crowded', color: 'rose', emoji: '🔴' },
};

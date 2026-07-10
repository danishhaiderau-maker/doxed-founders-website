/**
 * Idea Validator — types for the synthesis result payload stored in
 * IdeaCheck.resultJson. Mirrors the JSON schema in
 * docs/FOUNDER-IDEA-VALIDATOR.md §5.2.
 */

export type IdeaVerdict = 'novel' | 'empty' | 'moderate' | 'crowded';

export interface CompetitorEntry {
  name: string;
  /** "oss" | "product" | "startup" */
  type: string;
  url: string;
  description: string;
  stars?: number;
  traction?: string;
  funding?: string;
  /** What's different about the founder's idea vs this competitor. */
  differentiation: string;
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

/**
 * The full competitive-landscape payload. Stored as IdeaCheck.resultJson
 * and returned to the frontend verbatim. Produced by the synthesis model
 * (GLM 5.2 / DeepSeek via the AI Gateway) and parsed defensively.
 */
export interface IdeaValidationReport {
  verdict: IdeaVerdict;
  summary: string;
  differentiation: string;
  /** 0-100, derived from the synthesis model's verdict + competitor count. */
  differentiationScore: number;
  competitors: CompetitorEntry[];
  openSourceReuse: OpenSourceReuseEntry[];
}

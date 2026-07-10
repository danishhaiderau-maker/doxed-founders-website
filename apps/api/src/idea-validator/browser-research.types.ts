/**
 * Browser Research Adapter — types for the first real LAM (Large Action
 * Model) slice. See docs/FOUNDER-IDEA-VALIDATOR.md Part D.
 *
 * The research hand is an Execution Engine-style adapter that drives a
 * headless browser (Playwright/Chromium). The decision model — which link
 * to click, what to extract — is DeepSeek V4-Pro / GLM via the AI Gateway,
 * NOT a direct API call. This is the "Browser Use" pattern: the LLM sees
 * the page, decides the next action, the adapter executes it, loop until
 * the data is gathered or the step cap is hit.
 */

/**
 * Which site to search. Each target has its own URL template + extraction
 * rules baked into the adapter. `web` is a general DuckDuckGo HTML search
 * (no API key required, lenient rate limits) used as the catch-all.
 */
export type ResearchTarget = 'github' | 'producthunt' | 'web';

/**
 * A single discovered project / product from a research pass.
 * Fields are optional because different sites expose different data
 * (GitHub has stars; Product Hunt has upvotes; web hits have neither).
 */
export interface ResearchHit {
  name: string;
  description: string;
  url: string;
  /** GitHub stars, Product Hunt upvotes, or undefined if not available. */
  stars?: number;
  /** Free-text traction / funding signal if surfaced. */
  traction?: string;
  /** License SPDX id, when detected from a repo's metadata. */
  license?: string;
  /** Where this hit came from. */
  source: ResearchTarget;
}

/**
 * Input to a single research query.
 */
export interface ResearchQuery {
  query: string;
  targets: ResearchTarget[];
}

/**
 * Result of running one query across one or more targets.
 */
export interface ResearchResult {
  query: string;
  hits: ResearchHit[];
  /** Number of browser steps the LLM drove for this query. */
  stepsTaken: number;
  /** Wall-clock ms spent on this query. */
  elapsedMs: number;
  /** True if the per-query timeout fired before the step cap. */
  timedOut: boolean;
}

/**
 * A single "Browser Use" step. The LLM emits one of these per turn; the
 * adapter executes it and feeds the resulting page state back. Kept as a
 * discriminated union so the adapter can switch cleanly.
 */
export type BrowserAction =
  | { type: 'navigate'; url: string }
  | { type: 'extract'; reason: string }
  | { type: 'click'; selector: string }
  | { type: 'done'; reason: string };

/**
 * The page state handed to the decision model each step. Trimmed to stay
 * inside token budgets — full HTML would blow the context window.
 */
export interface PageSnapshot {
  url: string;
  title: string;
  /** Visible text content, truncated to ~2k chars. */
  text: string;
  /** Top links on the page as {text, href} for the model to pick from. */
  links: Array<{ text: string; href: string }>;
  /** Step number in the loop, 1-indexed. */
  step: number;
}

/**
 * Cost/latency budget for a single idea check. Defaults bound per-check
 * cost to ~$0.005 and wall-clock to <120s. See design doc Part D §5/§6.
 */
export interface ResearchBudget {
  /** Max LLM-driven browser steps per query (default 8). */
  maxStepsPerQuery: number;
  /** Hard timeout per query, ms (default 30000). */
  timeoutPerQueryMs: number;
  /** Hard timeout for the whole idea check, ms (default 120000). */
  timeoutTotalMs: number;
  /** Max hits to keep per query (default 10). */
  maxHitsPerQuery: number;
}

export const DEFAULT_RESEARCH_BUDGET: ResearchBudget = {
  maxStepsPerQuery: 8,
  timeoutPerQueryMs: 30_000,
  timeoutTotalMs: 120_000,
  maxHitsPerQuery: 10,
};

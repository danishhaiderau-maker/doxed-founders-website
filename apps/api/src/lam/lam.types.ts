/**
 * LAM (Large Action Model) — Phase 9 type contracts.
 *
 * The LAM layer is the "hands" to the AI Gateway's "brain." A founder
 * submits a natural-language goal ("research competitor X and summarize
 * pricing"); the LAM orchestrator asks the AI Gateway to plan it into
 * discrete steps, then executes each step through the right adapter
 * (BrowserAdapter for web, ComputerUseAdapter for desktop control).
 *
 * Every model call goes through AiProxyRuntimeService so the Flight
 * Recorder captures the full plan trace and DDollar is metered.
 */

/**
 * Adapter surface the orchestrator can drive. Browser is always
 * available; Computer-Use is gated to VERIFIED_BUILDER + the
 * COMPUTER_USE_ENABLED flag.
 */
export type LamAdapterId = 'browser' | 'computer-use';

/**
 * A single discrete step in a planned LAM task. The AI Gateway emits
 * these as JSON; the orchestrator executes them in order.
 */
export interface LamStep {
  /** 1-indexed position in the plan. */
  index: number;
  /** Human-readable description of what this step does. */
  description: string;
  /** Which adapter should run this step. */
  adapter: LamAdapterId;
  /** Adapter-specific payload (see BrowserStepPayload / ComputerUseStepPayload). */
  payload: unknown;
}

/**
 * Payload variants for a step. The orchestrator narrows based on
 * `adapter`; each adapter knows its own subset.
 */
export interface BrowserStepPayload {
  action:
    | 'navigate'
    | 'extract'
    | 'click'
    | 'fillForm'
    | 'screenshot'
    | 'research';
  url?: string;
  selector?: string;
  /** For fillForm: { selector, value } pairs. */
  fields?: Array<{ selector: string; value: string }>;
  /** For research: the natural-language query to plan + run. */
  query?: string;
}

export interface ComputerUseStepPayload {
  action: 'screenshot' | 'mouseMove' | 'click' | 'type' | 'key';
  x?: number;
  y?: number;
  text?: string;
  combo?: string;
}

/**
 * Result of executing one step. Kept terse so the step log stays
 * readable in the UI; large outputs (page HTML, screenshots) are
 * referenced by artifact path rather than inlined.
 */
export interface LamStepResult {
  index: number;
  status: 'success' | 'failed' | 'skipped';
  /** Short summary the adapter / model produced. */
  summary: string;
  /** Artifacts produced (screenshot paths, extracted text refs, URLs). */
  artifacts?: string[];
  /** Set on failure. */
  error?: string;
  startedAt: string;
  completedAt: string;
}

/**
 * Task lifecycle states. PLANNING = AI Gateway is breaking the goal
 * into steps; RUNNING = steps are executing; the terminal states are
 * COMPLETED / FAILED.
 */
export type LamTaskStatus =
  | 'PLANNING'
  | 'RUNNING'
  | 'SYNTHESIZING'
  | 'COMPLETED'
  | 'FAILED';

/**
 * The full task record the orchestrator mutates as it runs. Returned
 * by GET /api/lam/task/:id so the frontend can render live progress.
 */
export interface LamTask {
  id: string;
  userId: string;
  goal: string;
  status: LamTaskStatus;
  /** The plan the AI Gateway produced. Empty during PLANNING. */
  steps: LamStep[];
  /** Per-step results as they land. */
  results: LamStepResult[];
  /** Free-text final answer / summary the model produced. */
  result?: string;
  /** Total wall-clock duration in ms; set on terminal states. */
  elapsedMs?: number;
  /** Estimated cost in USD; populated from DDollar spend. */
  costDdollar?: number;
  /** Error message on FAILED. */
  error?: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * What the planner model is asked to return. Strict JSON so the
 * orchestrator can parse defensively (same discipline as the Phase 6
 * BrowserResearchAdapter's BrowserAction).
 */
export interface LamPlan {
  steps: Array<{
    description: string;
    adapter: LamAdapterId;
    payload: unknown;
  }>;
}

/**
 * The adapter availability descriptor returned by
 * GET /api/lam/adapters.
 */
export interface LamAdapterStatus {
  id: LamAdapterId;
  available: boolean;
  /** Why it's unavailable, when it isn't. */
  reason?: string;
  /** True when the adapter is premium-tier only. */
  premium?: boolean;
}

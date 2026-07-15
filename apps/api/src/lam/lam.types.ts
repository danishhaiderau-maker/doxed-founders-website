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

// ---------------------------------------------------------------------------
// Phase 9 — Computer-Use execution surface
// ---------------------------------------------------------------------------

/**
 * The action block Claude emits inside a `tool_use` content block when it
 * decides to drive the virtual computer. Mirrors the Anthropic
 * `computer_20250124` tool action union (subset we execute).
 *
 * `coordinate` is `[x, y]` in display pixels; `text` is the string for the
 * `type` action; `key` is a hyphen-separated combo for the `key` action
 * (e.g. `Return`, `ctrl-s`); `scrollAmount` is signed pixels for `scroll`.
 *
 * The optional `duration` is a hint some action types accept; we forward
 * it when present and ignore it when not supported by the target.
 */
export type ComputerUseAction =
  | { type: 'screenshot' }
  | { type: 'mouse_move'; coordinate: [number, number] }
  | { type: 'left_click'; coordinate?: [number, number] }
  | { type: 'right_click'; coordinate?: [number, number] }
  | { type: 'middle_click'; coordinate?: [number, number] }
  | { type: 'double_click'; coordinate?: [number, number] }
  | { type: 'left_click_drag'; start_coordinate: [number, number]; coordinate: [number, number] }
  | { type: 'type'; text: string }
  | { type: 'key'; key: string }
  | { type: 'scroll'; coordinate?: [number, number]; scroll_direction: 'up' | 'down'; scroll_amount: number }
  | { type: 'wait'; duration?: number }
  | { type: 'cursor_position' };

/**
 * Result of executing one Claude-issued action against an ExecutionTarget.
 * `screenshotBase64` is only populated by the `screenshot` action (and is
 * what gets fed back to Claude as the next tool_result).
 */
export interface ExecutionTargetResult {
  ok: boolean;
  /** Optional human-readable summary for the step log. */
  summary?: string;
  /** Base64-encoded PNG, only for `screenshot` actions. */
  screenshotBase64?: string;
  /** Set when ok === false. */
  error?: string;
}

/**
 * ExecutionTarget — the abstraction a ComputerUseAdapter drives.
 *
 * Two implementations live alongside the adapter:
 *   - `PlaywrightTarget`  — drives a headless Chromium page. Docker-safe,
 *                           already proven by the Phase 6 BrowserResearch
 *                           adapter. Default.
 *   - `RealScreenTarget`  — drives the host's real display via nut-js /
 *                           robotjs. Requires native modules and a real
 *                           display; not safe in CI / containers. The
 *                           factory returns a stub that throws clearly
 *                           when native deps are missing.
 *
 * Both implementations must round-trip a `screenshot` → base64 PNG so the
 * adapter can hand it back to Claude as the next tool_result. Pixel
 * coordinates are in the target's own coordinate space (the display
 * dimensions the adapter advertises to Claude via the
 * `computer_20250124` tool definition).
 */
export interface ExecutionTarget {
  readonly id: 'browser' | 'screen';
  /** Display dimensions advertised to Claude (px). */
  readonly displayWidthPx: number;
  readonly displayHeightPx: number;
  /** Lazy lifecycle so the adapter can boot on first use. */
  start(): Promise<void>;
  /** Reap any backing process (browser / native session). Idempotent. */
  stop(): Promise<void>;
  /** True once start() has succeeded and stop() hasn't been called. */
  isRunning(): boolean;
  /** Execute one Claude action; return a result the adapter can feed back. */
  execute(action: ComputerUseAction): Promise<ExecutionTargetResult>;
}

/**
 * Snapshot of one Claude tool_use round-trip. Used for the durable log
 * (LamTask.currentStep / lastToolCallId) and for the per-step history the
 * orchestrator persists so a crashed task resumes mid-loop, not from zero.
 */
export interface ComputerUseToolCall {
  /** Anthropic's `id` of the tool_use block (durable resume key). */
  toolUseId: string;
  /** The action Claude asked for (already narrowed). */
  action: ComputerUseAction;
  /** Outcome of executing `action` against the target. */
  result: ExecutionTargetResult;
  /** ISO timestamps so the row is replayable. */
  startedAt: string;
  completedAt: string;
}

/**
 * Tier of confirmation a Computer-Use run enforces. Mirrors
 * LAM_REQUIRE_CONFIRMATION env var — when set to `1`, destructive actions
 * (clicks on forms / delete buttons / submits) must round-trip through an
 * external confirmer; otherwise the agent loop auto-confirms but still
 * logs the destructive intent.
 */
export type ConfirmationState =
  | { kind: 'auto-confirmed'; summary: string }
  | { kind: 'pending'; summary: string }
  | { kind: 'confirmed'; summary: string }
  | { kind: 'denied'; summary: string };

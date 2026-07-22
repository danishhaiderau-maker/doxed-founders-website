/**
 * Founder Stack runtime status contract (Phase 3).
 *
 * Canonical shape of the "Founder IDE status" object surfaced by the Founder
 * Node tray and consumed by:
 *  - the web settings page (Workstream C renders it),
 *  - the IDE extension status bar (Workstream B reads it via IPC),
 *  - the updater (Workstream E reads `updateState` to coordinate restarts).
 *
 * The object is deliberately flat (no nested objects) so it serializes
 * cleanly to JSON and renders as a single-row table in the UI. Every field
 * has a documented source and staleness rule — consumers MUST tolerate
 * stale values (don't 500 a status panel because the gateway probe is 31s
 * old).
 *
 * Staleness vs. liveness:
 *  - "freshness = real-time" means the field updates immediately when the
 *    underlying state changes (no polling delay).
 *  - "freshness = ≤Ns" means the value is refreshed at most every N seconds;
 *    older values are still valid to display but should be marked "stale".
 */

/**
 * Updater state machine. Workstream E owns the transitions; Workstream C
 * only renders the current value. `failed` is terminal until the user
 * explicitly retries; `rolling_back` is transient (seconds).
 */
export type FounderStackUpdateState =
  | 'idle'
  | 'downloading'
  | 'verifying'
  | 'installing'
  | 'rolling_back'
  | 'failed';

/**
 * Execution consent state. The user grants consent for a single task; it
 * expires after 5 minutes of inactivity to prevent "I walked away from the
 * keyboard and the agent kept going" foot-guns. `denied` is explicit
 * rejection; `expired` is the auto-expiry transition.
 */
export type ExecutionConsentState = 'granted' | 'pending' | 'denied' | 'expired';

/**
 * The status object. Every field is required (no `?`) so consumers can
 * destructure without null-checking — `null` is used for "no value yet"
 * (e.g. `workspace` before any workspace message has arrived).
 */
export interface FounderStackRuntimeStatus {
  /**
   * Version of the Founder Node app currently running. From
   * `app.getVersion()` in the Electron main process. Freshness = real-time
   * (only changes on restart after an update).
   */
  installedVersion: string;

  /**
   * Latest version available, from `GET /api/founder-node/manifest`
   * `latestVersion` field. Cached for 60s on the node side to avoid hammering
   * the API. Freshness = ≤60s (cache TTL).
   */
  latestVersion: string;

  /**
   * Whether the Founder Node tray is running and emitting heartbeats.
   * Derived from the heartbeat timestamp being within `ONLINE_WINDOW_MS`
   * (5 minutes). Freshness = real-time.
   */
  founderNodeOnline: boolean;

  /**
   * Whether the IDE↔node IPC handshake is currently established. Flips to
   * true on `hello` + successful `authState`, false on pipe close. Freshness
   * = ≤15s (heartbeat interval — if no heartbeat in 15s, treat as down).
   */
  ideHandshakeActive: boolean;

  /**
   * Whether the last Gateway health probe succeeded. The node probes
   * `GET /api/health` (or equivalent) every 30s. Freshness = ≤30s.
   */
  gatewayReachable: boolean;

  /**
   * Whether the node is paired: `node-config.json` exists with a valid
   * `founderId` + `nodeId` + `nodeToken` (token not expired). Freshness =
   * real-time (file watch).
   */
  paired: boolean;

  /**
   * Current workspace path, or null if no folder is open in the IDE.
   * Mirrors the last `workspace` IPC message. Freshness = ≤15s.
   */
  workspace: string | null;

  /**
   * ISO 8601 timestamp of the last received heartbeat from any source
   * (node tray, IDE, or gateway). Used to compute "online" windows.
   */
  lastHeartbeat: string;

  /**
   * Current updater state. See `FounderStackUpdateState`. Freshness =
   * real-time (state machine transitions are synchronous).
   */
  updateState: FounderStackUpdateState;

  /**
   * Current execution consent state. See `ExecutionConsentState`. Freshness
   * = real-time; expires after 5 minutes of inactivity (the state machine
   * auto-flips `granted` → `expired`).
   */
  executionConsentState: ExecutionConsentState;
}

/**
 * Staleness thresholds (milliseconds) for each derived field. Consumers use
 * these to decide whether to render a value as "live" or "stale". Exported
 * so tests + UI agree on the numbers.
 */
export const STATUS_STALENESS_MS = {
  /** Gateway probe interval. */
  gatewayReachable: 30_000,
  /** IDE heartbeat interval. */
  ideHandshakeActive: 15_000,
  /** Workspace IPC interval. */
  workspace: 15_000,
  /** Manifest cache TTL on the node. */
  latestVersion: 60_000,
  /** Node "online" window — matches the API's ONLINE_WINDOW_MS. */
  founderNodeOnline: 5 * 60_000,
  /** Consent auto-expiry. */
  executionConsent: 5 * 60_000,
} as const;

/**
 * The 6 valid `updateState` values. Exported as a const array so consumers
 * (and tests) can iterate without importing the type.
 */
export const FOUNDER_STACK_UPDATE_STATES: readonly FounderStackUpdateState[] = [
  'idle',
  'downloading',
  'verifying',
  'installing',
  'rolling_back',
  'failed',
] as const;

/**
 * The 4 valid `executionConsentState` values.
 */
export const EXECUTION_CONSENT_STATES: readonly ExecutionConsentState[] = [
  'granted',
  'pending',
  'denied',
  'expired',
] as const;

/**
 * Type guard for `FounderStackUpdateState`. Useful when parsing status from
 * an untrusted source (e.g. a status JSON file written by an older node).
 */
export function isFounderStackUpdateState(v: unknown): v is FounderStackUpdateState {
  return typeof v === 'string' && FOUNDER_STACK_UPDATE_STATES.includes(v as FounderStackUpdateState);
}

/**
 * Type guard for `ExecutionConsentState`.
 */
export function isExecutionConsentState(v: unknown): v is ExecutionConsentState {
  return typeof v === 'string' && EXECUTION_CONSENT_STATES.includes(v as ExecutionConsentState);
}

/**
 * Build a default "everything offline" status. Useful as the initial state
 * before the first heartbeat arrives — consumers render this and then patch
 * fields as live data streams in.
 */
export function emptyFounderStackRuntimeStatus(): FounderStackRuntimeStatus {
  const now = new Date(0).toISOString();
  return {
    installedVersion: '',
    latestVersion: '',
    founderNodeOnline: false,
    ideHandshakeActive: false,
    gatewayReachable: false,
    paired: false,
    workspace: null,
    lastHeartbeat: now,
    updateState: 'idle',
    executionConsentState: 'pending',
  };
}

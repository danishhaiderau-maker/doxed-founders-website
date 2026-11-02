/**
 * Canonical showcase URL remains the historical Fly host for identity locks /
 * dashboard_url checks. The Fly app itself was DELETED (OBSERVED 2026-11-03);
 * Railway must not poll, wake, or report to that dead host.
 */
export const CANONICAL_SHOWCASE_BOT_URL = 'https://doxed-btc-bot.fly.dev';

/** Hard-retired: Fly app gone. Prefer offline-by-design over retry storms. */
export const FLY_SHOWCASE_RUNTIME_RETIRED = true;

/** Stable dashboard label (HM condition: not a generic error). */
export const SHOWCASE_OFFLINE_BY_DESIGN_LABEL =
  'offline, by design — moving to laptop';

export type ShowcaseOfflineByDesignStatus = {
  status: 'offline';
  label: typeof SHOWCASE_OFFLINE_BY_DESIGN_LABEL;
  reason: 'FLY_APP_DELETED';
  offline_by_design: true;
  moving_to_laptop: true;
  /** Fail-closed: never true from a cache path after Fly is gone. */
  live_armed: false;
  bitfinex_live_enabled: false;
  botConnected: false;
  flyReachable: false;
  /** Clients must not spin / retry-storm on this response. */
  retry: false;
  error_loop: false;
};

export function normalizeShowcaseBotUrl(value: string | null | undefined): string {
  return String(value ?? '').trim().replace(/\/$/, '');
}

export function isCanonicalShowcaseBotUrl(value: string | null | undefined): boolean {
  return normalizeShowcaseBotUrl(value) === CANONICAL_SHOWCASE_BOT_URL;
}

/**
 * Production default is retired. Test-only escape hatch
 * `FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE=1` lets legacy Fly-path unit tests exercise
 * fetch logic without hammering a dead host in Railway.
 */
export function isFlyShowcaseRuntimeRetired(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  if (env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE === '1') return false;
  return FLY_SHOWCASE_RUNTIME_RETIRED;
}

export function buildShowcaseOfflineByDesignStatus(): ShowcaseOfflineByDesignStatus {
  return {
    status: 'offline',
    label: SHOWCASE_OFFLINE_BY_DESIGN_LABEL,
    reason: 'FLY_APP_DELETED',
    offline_by_design: true,
    moving_to_laptop: true,
    live_armed: false,
    bitfinex_live_enabled: false,
    botConnected: false,
    flyReachable: false,
    retry: false,
    error_loop: false,
  };
}

/** True when a URL still targets the deleted Fly app (or any *.fly.dev host). */
export function isDeadFlyHostUrl(value: string | null | undefined): boolean {
  const normalized = normalizeShowcaseBotUrl(value);
  if (!normalized) return false;
  try {
    const host = new URL(normalized).hostname.toLowerCase();
    return host === 'doxed-btc-bot.fly.dev' || host.endsWith('.fly.dev');
  } catch {
    return /doxed-btc-bot\.fly\.dev/i.test(normalized);
  }
}

/**
 * RELAY_EXECUTOR_WAKE_URL must not wake a deleted Fly host. While the
 * showcase Fly runtime is retired, drop the wake path entirely (no new wake).
 */
export function shouldDispatchRelayExecutorWake(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  if (isFlyShowcaseRuntimeRetired(env)) return false;
  const base = env.RELAY_EXECUTOR_WAKE_URL?.trim();
  if (!base) return false;
  if (isDeadFlyHostUrl(base)) return false;
  return true;
}

/**
 * R51 — single registration-site gate for Neon-chatting background timers.
 * When Fly showcase runtime is retired, do not register the 20s position-alert
 * poll, durable wake poll / keepalive, or 5m signal-cycle idle recovery (and
 * sibling showcase session sync / Fly reporter). Backend stays up for Vercel;
 * Neon can sleep. Call ONLY at timer registration sites — never scatter
 * early-returns inside timer bodies.
 */
export function shouldRegisterNeonBackgroundTimers(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return !isFlyShowcaseRuntimeRetired(env);
}

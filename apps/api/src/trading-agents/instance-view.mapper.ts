import type { BotActivityEntry } from './bot-state.mapper';

export const USER_INSTANCE_STARTING_BALANCE = 500;

export type UserInstanceScope = {
  sessionStartedAt: Date;
  startingBalanceUsd: number;
  instanceMode: 'copy' | 'live';
  instanceId: string;
};

export type UserInstanceStats = {
  balanceUsd: number;
  equityUsd: number;
  netReturnPct: number;
  tradeCount: number;
  winRatePct: number;
  sessionStartedAt: string;
  startingBalanceUsd: number;
  instanceMode: 'copy' | 'live';
  sessionPnlUsd?: number;
};

/** Participants visible in the current live-copy / relay-sim session window. */
export function participantTouchesSession(
  p: { createdAt: Date; updatedAt: Date; events: Array<{ createdAt: Date }> },
  sessionStart: Date,
): boolean {
  return (
    p.createdAt >= sessionStart ||
    p.updatedAt >= sessionStart ||
    p.events.some((e) => e.createdAt >= sessionStart)
  );
}

export function readInstanceScope(instance: {
  id: string;
  activatedAt: Date | null;
  hiredAt: Date;
  exchangeProvider: string;
  dashboardState: unknown;
}): UserInstanceScope {
  const dash = (instance.dashboardState ?? {}) as Record<string, unknown>;
  const mode: 'copy' | 'live' =
    dash.instanceMode === 'copy' || instance.exchangeProvider === 'paper' ? 'copy' : 'live';
  // Live desk P&L / completed trades must scope to this Start (arm), not the
  // long-lived collection sessionStartedAt (often days old). Prefer the live
  // arm timestamps; liveDeskSessionStartedAt survives mismatch auto-pause.
  const liveArmIso =
    mode === 'live'
      ? ([dash.relayArmedAt, dash.realTradingConfirmedAt, dash.liveDeskSessionStartedAt].find(
          (value) => typeof value === 'string' && value.trim(),
        ) as string | undefined)
      : undefined;
  const sessionIso =
    liveArmIso ||
    (typeof dash.sessionStartedAt === 'string' && dash.sessionStartedAt) ||
    instance.activatedAt?.toISOString() ||
    instance.hiredAt.toISOString();
  const startingBalanceUsd =
    typeof dash.startingBalanceUsd === 'number'
      ? dash.startingBalanceUsd
      : typeof dash.paperAllocationUsd === 'number'
        ? dash.paperAllocationUsd
        : USER_INSTANCE_STARTING_BALANCE;

  return {
    instanceId: instance.id,
    sessionStartedAt: new Date(sessionIso),
    startingBalanceUsd,
    instanceMode: mode,
  };
}

/** Fidelity must use the same epoch as execution.
 *
 * A live instance does not inherit historical relay-sim misses. Live execution
 * is NEXT_FRESH_ONLY and writes relayArmedAt on every explicit Start, so there
 * is no live fidelity session before that timestamp exists.
 */
export function readRelayFidelitySessionStart(input: {
  instanceMode: 'copy' | 'live';
  dashboardState: unknown;
  copyRelaySim?: { active?: boolean; startedAt?: string | null } | null;
  userSessionStartedAt?: string | null;
}): Date | null {
  const dash = (input.dashboardState ?? {}) as Record<string, unknown>;
  if (input.instanceMode === 'live') {
    for (const raw of [dash.relayArmedAt, dash.realTradingConfirmedAt]) {
      if (typeof raw !== 'string' || !raw.trim()) continue;
      const parsed = new Date(raw);
      if (Number.isFinite(parsed.getTime())) return parsed;
    }
    return null;
  }

  const raw =
    input.copyRelaySim?.active && input.copyRelaySim.startedAt
      ? input.copyRelaySim.startedAt
      : input.userSessionStartedAt;
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function parseActivityTime(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

/** Keep only trades/activity after the user started their own session; recompute balance from their start. */
export function scopeActivityToUserSession(
  activity: BotActivityEntry[],
  scope: UserInstanceScope,
): BotActivityEntry[] {
  const startMs = scope.sessionStartedAt.getTime();
  const chronological = activity
    .filter((row) => parseActivityTime(row.createdAt) >= startMs - 1000)
    .sort((a, b) => parseActivityTime(a.createdAt) - parseActivityTime(b.createdAt));

  let balance = scope.startingBalanceUsd;
  const withBalance = chronological.map((row) => {
    const net =
      row.netPnlUsd ??
      (row.profitPct != null ? (Number(row.profitPct) / 100) * scope.startingBalanceUsd : 0);
    balance += net;
    return {
      ...row,
      id: `user-${scope.instanceId}-${row.id}`,
      balanceUsd: Number(balance.toFixed(2)),
      netPnlUsd: net != null ? Number(Number(net).toFixed(2)) : row.netPnlUsd,
    };
  });

  return withBalance.reverse();
}

export function statsFromScopedActivity(
  activity: BotActivityEntry[],
  scope: UserInstanceScope,
): UserInstanceStats {
  const executed = activity.filter((row) => {
    const t = row.type.toUpperCase();
    return t.includes('TRADE') || t.includes('POSITION');
  });
  const wins = executed.filter(
    (row) => (row.netPnlUsd ?? 0) > 0 || (row.profitPct ?? 0) > 0,
  ).length;
  const winRate = executed.length > 0 ? (wins / executed.length) * 100 : 0;
  const lastBalance = executed.length > 0 ? executed[0].balanceUsd : scope.startingBalanceUsd;
  const equity = lastBalance ?? scope.startingBalanceUsd;
  const sessionPnlUsd = equity - scope.startingBalanceUsd;
  const netReturnPct =
    scope.startingBalanceUsd > 0
      ? (sessionPnlUsd / scope.startingBalanceUsd) * 100
      : sessionPnlUsd !== 0
        ? sessionPnlUsd
        : 0;

  return {
    balanceUsd: Number(equity.toFixed(2)),
    equityUsd: Number(equity.toFixed(2)),
    netReturnPct: Number(netReturnPct.toFixed(2)),
    tradeCount: executed.length,
    winRatePct: Number(winRate.toFixed(1)),
    sessionStartedAt: scope.sessionStartedAt.toISOString(),
    startingBalanceUsd: scope.startingBalanceUsd,
    instanceMode: scope.instanceMode,
    sessionPnlUsd: Number(sessionPnlUsd.toFixed(2)),
  };
}

export function buildFreshInstanceDashboardState(
  mode: 'copy' | 'live',
  allocationUsd = USER_INSTANCE_STARTING_BALANCE,
  extra?: Record<string, unknown>,
) {
  const now = new Date().toISOString();
  return {
    instanceMode: mode,
    copySource: 'admin-showcase',
    startingBalanceUsd: allocationUsd,
    liveSessionStartingBalanceUsd: mode === 'live' ? allocationUsd : undefined,
    sessionStartedAt: now,
    paperAllocationUsd: mode === 'copy' ? allocationUsd : undefined,
    ...extra,
  };
}

/**
 * A Showcase fresh-collection reset intentionally clears session counters, but
 * it is not a user Stop. Preserve a previously explicit live arm so an ACTIVE
 * relay cannot become a misleading "ACTIVE but relay-not-armed" instance.
 *
 * This deliberately never manufactures an arm from status alone: the arm must
 * already be a parseable timestamp written by an explicit Start action.
 */
export function activeLiveRelayArmForSessionReset(
  status: string,
  dashboardState: Record<string, unknown>,
): Record<string, unknown> {
  // The reset replaces the whole dashboard JSON. Preserve an explicit
  // disarmed state as well as active arms, otherwise PAUSED becomes missing
  // mode and fails the strict deployment boundary after every session reset.
  // Status remains authoritative even when a legacy snapshot has arm fields.
  if (status === 'PAUSED') {
    return {
      relayExecutionMode: 'PAUSED',
      relayArmedAt: null,
      realTradingConfirmedAt: null,
    };
  }
  if (status !== 'ACTIVE' || dashboardState.relayExecutionMode !== 'LIVE') return {};

  const validIso = (value: unknown): string | null => {
    if (typeof value !== 'string' || !value.trim()) return null;
    return Number.isFinite(Date.parse(value)) ? value : null;
  };
  const relayArmedAt = validIso(dashboardState.relayArmedAt);
  if (!relayArmedAt) return {};

  const realTradingConfirmedAt = validIso(dashboardState.realTradingConfirmedAt) ?? relayArmedAt;
  const liveDeskSessionStartedAt =
    validIso(dashboardState.liveDeskSessionStartedAt) ?? relayArmedAt;

  return {
    relayExecutionMode: 'LIVE',
    relayArmedAt,
    realTradingConfirmedAt,
    liveDeskSessionStartedAt,
    relayEntryPolicy:
      typeof dashboardState.relayEntryPolicy === 'string'
        ? dashboardState.relayEntryPolicy
        : 'NEXT_FRESH_ONLY',
    ...(dashboardState.relayPolicyVersion != null
      ? { relayPolicyVersion: dashboardState.relayPolicyVersion }
      : {}),
    ...(dashboardState.relayMirrorLanes != null
      ? { relayMirrorLanes: dashboardState.relayMirrorLanes }
      : {}),
    ...(dashboardState.relayExecutorAtArm != null
      ? { relayExecutorAtArm: dashboardState.relayExecutorAtArm }
      : {}),
    ...(dashboardState.relayLastTransition != null
      ? { relayLastTransition: dashboardState.relayLastTransition }
      : {}),
  };
}

/** Merge a partial dashboard patch without clobbering nested relay-sim fields. */
export function applyDashboardPatch(
  dash: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const next = { ...dash, ...patch };
  if (
    patch.copyRelaySim &&
    dash.copyRelaySim &&
    typeof patch.copyRelaySim === 'object' &&
    typeof dash.copyRelaySim === 'object'
  ) {
    next.copyRelaySim = {
      ...(dash.copyRelaySim as Record<string, unknown>),
      ...(patch.copyRelaySim as Record<string, unknown>),
    };
  }
  return next;
}

/**
 * Apply background telemetry while preserving the authoritative relay
 * lifecycle invariant. A PAUSED instance must never regain LIVE arm metadata
 * from an in-flight writer holding a pre-pause dashboard JSON snapshot.
 */
export function applyInstanceDashboardPatch(
  status: string,
  dash: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const next = applyDashboardPatch(dash, patch);
  if (status === 'PAUSED') {
    next.relayExecutionMode = 'PAUSED';
    next.relayArmedAt = null;
    next.realTradingConfirmedAt = null;
  }
  return next;
}

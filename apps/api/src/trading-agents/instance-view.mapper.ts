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
};

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
  const sessionIso =
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
  const netReturnPct =
    ((equity - scope.startingBalanceUsd) / scope.startingBalanceUsd) * 100;

  return {
    balanceUsd: scope.startingBalanceUsd,
    equityUsd: Number(equity.toFixed(2)),
    netReturnPct: Number(netReturnPct.toFixed(2)),
    tradeCount: executed.length,
    winRatePct: Number(winRate.toFixed(1)),
    sessionStartedAt: scope.sessionStartedAt.toISOString(),
    startingBalanceUsd: scope.startingBalanceUsd,
    instanceMode: scope.instanceMode,
  };
}

export function buildFreshInstanceDashboardState(
  mode: 'copy' | 'live',
  paperAllocationUsd = USER_INSTANCE_STARTING_BALANCE,
  extra?: Record<string, unknown>,
) {
  const now = new Date().toISOString();
  return {
    instanceMode: mode,
    copySource: 'admin-showcase',
    startingBalanceUsd: paperAllocationUsd,
    sessionStartedAt: now,
    paperAllocationUsd: mode === 'copy' ? paperAllocationUsd : undefined,
    ...extra,
  };
}

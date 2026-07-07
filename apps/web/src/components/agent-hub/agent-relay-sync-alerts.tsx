'use client';

import {
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  type CopyRelayLimitChainSnapshot,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type TradeLifecycleIntegritySnapshot,
  type RelaySimParticipantStats,
} from '@dcf/utils';
import type { RelayFidelitySnapshot } from '@/components/agent-hub/agent-relay-fidelity-panel';

type Alert = { level: 'error' | 'warn' | 'info'; title: string; detail: string };

export function buildRelaySyncAlerts(input: {
  mode: 'live' | 'sim';
  botConnected?: boolean;
  /**
   * F3 circuit-breaker error from the TradingAgentInstance, written within ~60s
   * of a real outage. Used to detect the stale-display-cache window where
   * botConnected still shows true via cached snapshot but the execution path is
   * already known unreachable upstream.
   */
  instanceLastError?: string | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relaySimParticipantStats?: RelaySimParticipantStats | null;
  relayFidelity?: RelayFidelitySnapshot | null;
}): Alert[] {
  const alerts: Alert[] = [];
  const reconcile = input.copyRelayReconcile ?? input.copyRelaySim?.reconcile ?? null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaBad =
    reconcile?.alert ?? Math.abs(delta) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC;

  // F3 writes one of these substrings to instance.lastError when an outage
  // crosses ~60s (see bot-bridge.service.ts circuit-breaker). The display cache
  // behind botConnected can lag up to 10min, so we treat either signal as
  // sufficient to surface the offline alert — closes the 1-10min stale window.
  const f3Outage =
    typeof input.instanceLastError === 'string' &&
    /safe mode|Showcase unreachable|Showcase outage/i.test(input.instanceLastError);
  if (input.botConnected === false || f3Outage) {
    const needsShowcase =
      input.mode === 'live' || (input.mode === 'sim' && Boolean(input.copyRelaySim?.active));
    if (needsShowcase) {
      alerts.push({
        level: 'error',
        title: 'Showcase bot offline',
        detail:
          'Global showcase bot (:7002 via tunnel) is not reachable from the site API — relay cannot mirror new signals. On Home Command Center: confirm tunnel is online, then click Wire to site.',
      });
    }
  }

  if (deltaBad) {
    alerts.push({
      level: 'error',
      title: 'Ledger desync',
      detail: `Exchange qty ${reconcile?.exchangePositionQty.toFixed(5)} BTC vs ledger ${reconcile?.ledgerOpenQty.toFixed(5)} BTC (Δ ${delta >= 0 ? '+' : ''}${delta.toFixed(5)}). Virtual-lot reconcile is healing — do not go live until delta is near zero.`,
    });
  }

  const limitChain = input.copyRelayLimitChain;
  if (limitChain && !limitChain.aligned) {
    alerts.push({
      level: 'warn',
      title: 'Limit chain mismatch',
      detail: `Configured ${limitChain.configuredLimit ?? '—'} · relay ${limitChain.relayLimit ?? '—'} · execution ${limitChain.executionOpen}+${limitChain.executionPending}=${limitChain.executionTotal}. Pending limits may be blocked or over capacity.`,
    });
  }

  const lifecycle = input.tradeLifecycleIntegrity;
  if (lifecycle && lifecycle.sampleSize > 0 && lifecycle.integrityPct < 100) {
    const gap = lifecycle.recentGaps[0];
    const expiredGap = gap?.status === 'EXPIRED';
    alerts.push({
      level: 'warn',
      title: `Lifecycle gaps (${lifecycle.integrityPct}% complete)`,
      detail: expiredGap
        ? `${lifecycle.completeCount}/${lifecycle.sampleSize} trades have full round-trip lifecycle. Some expired before fill (ORDER→EXPIRED) — normal when showcase chase buckets defer or TTL expires.`
        : gap
          ? `${lifecycle.completeCount}/${lifecycle.sampleSize} trades have full ORDER→FILLED→EXIT. Example: ${gap.tradeId} missing ${gap.missingStages.join(', ')}.`
          : `${lifecycle.completeCount}/${lifecycle.sampleSize} relay trades missing lifecycle stages.`,
    });
  }

  const fidelity = input.relayFidelity;
  if (fidelity?.summary) {
    const missingEntry = fidelity.summary.missingShowcaseEntryCount ?? 0;
    const missingExit = fidelity.summary.missingShowcaseExitCount ?? 0;
    if (missingEntry > 0 || missingExit > 0) {
      alerts.push({
        level: 'warn',
        title: 'Local bot price gaps in fidelity',
        detail: `${missingEntry} trades missing global bot :7002 entry · ${missingExit} missing global bot exit. Usually means the home bot is offline, not wired, or trade IDs do not match — check tunnel + /api/state trades_map.`,
      });
    }
    if (
      fidelity.summary.maxEntryDeltaPct != null &&
      Math.abs(fidelity.summary.maxEntryDeltaPct) > 0.15
    ) {
      alerts.push({
        level: 'warn',
        title: 'Entry slippage vs showcase',
        detail: `Max entry delta ${fidelity.summary.maxEntryDeltaPct.toFixed(3)}% — merged Bitfinex position may fill away from showcase limit.`,
      });
    }
  }

  if (input.mode === 'sim' && input.copyRelaySim?.active) {
    const stats = input.relaySimParticipantStats;
    const terminal = stats ? stats.closed + stats.expired : 0;
    if (stats && terminal >= 5 && stats.expired / terminal >= 0.75) {
      alerts.push({
        level: 'warn',
        title: 'Most relay limits expiring before fill',
        detail: `${stats.expired} expired vs ${stats.closed} filled this sim session (${stats.pending} pending, ${stats.open} open). Usually means showcase chase deferred entry or limits rested away from market. Confirm global bot :7002 is wired, then restart sim after a fresh showcase session if fills stay near zero.`,
      });
    }

    const showcasePnl = input.copyRelaySim.showcasePnlUsd ?? 0;
    const simPnl = input.copyRelaySim.sessionPnlUsd ?? 0;
    const gap = simPnl - showcasePnl;
    if (Math.abs(gap) > 25) {
      alerts.push({
        level: 'warn',
        title: 'Sim P&L drift vs showcase',
        detail: `Paper sim ${gap >= 0 ? '+' : ''}$${gap.toFixed(2)} vs showcase this session — review virtual-lot exits before enabling live relay.`,
      });
    }
  }

  if (alerts.length === 0 && input.mode === 'sim') {
    alerts.push({
      level: 'info',
      title: 'Sim book synced',
      detail:
        'Paper relay tracks virtual $20 lots on merged BTC-PERP. Tables below show sim orders/positions; showcase reference appears when the admin bot fires the next signal.',
    });
  }

  return alerts;
}

export function AgentRelaySyncAlerts({
  alerts,
}: {
  alerts: Alert[];
}) {
  if (!alerts.length) return null;

  return (
    <div className="space-y-2">
      {alerts.map((a) => (
        <div
          key={`${a.level}-${a.title}`}
          className={`rounded-xl border px-4 py-3 text-xs ${
            a.level === 'error'
              ? 'border-red-500/50 bg-red-950/30 text-red-100'
              : a.level === 'warn'
                ? 'border-amber-500/45 bg-amber-950/25 text-amber-100'
                : 'border-sky-500/30 bg-sky-950/15 text-sky-100'
          }`}
        >
          <p className="text-[10px] font-bold uppercase tracking-[0.15em]">{a.title}</p>
          <p className="mt-1 leading-relaxed opacity-90">{a.detail}</p>
        </div>
      ))}
    </div>
  );
}

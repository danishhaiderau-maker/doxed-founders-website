'use client';

import Link from 'next/link';
import {
  BITFINEX_COPY_DEFAULT_MARGIN_USD,
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  formatUsd,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
} from '@dcf/utils';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { AgentDeskSwitcher } from '@/components/agent-hub/agent-desk-switcher';
import { AgentDeskMetricsBar } from '@/components/agent-hub/agent-desk-metrics-bar';
import {
  AgentRelaySyncAlerts,
  buildRelaySyncAlerts,
} from '@/components/agent-hub/agent-relay-sync-alerts';
import {
  AgentRelayFidelityPanel,
  type RelayFidelitySnapshot,
} from '@/components/agent-hub/agent-relay-fidelity-panel';
import type { TradingAgentSummary } from '@/lib/api';

export function CopyTradeDetailsStrip({
  agent,
  exchangeLabel,
  copyRelayReconcile,
  copyRelaySim,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relayFidelity,
  instanceStatus,
  botConnected,
  mode,
}: {
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relayFidelity?: RelayFidelitySnapshot | null;
  instanceStatus?: string | null;
  botConnected?: boolean;
  mode: 'live';
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const reconcile = copyRelayReconcile ?? copyRelaySim?.reconcile ?? null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaBad = reconcile?.alert ?? Math.abs(delta) > 0.001;
  const limitChain = copyRelayLimitChain;
  const limitBad = limitChain != null && !limitChain.aligned;
  const lifecycle = tradeLifecycleIntegrity;
  const lifecycleBad = lifecycle != null && lifecycle.integrityPct < 100;
  const paused = instanceStatus === 'PAUSED';
  const syncAlerts = buildRelaySyncAlerts({
    mode: 'live',
    botConnected,
    copyRelaySim,
    copyRelayReconcile: reconcile,
    copyRelayLimitChain: limitChain,
    tradeLifecycleIntegrity: lifecycle,
    relayFidelity,
  });

  return (
    <section className="rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-950/90 to-emerald-950/10 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-400">
          Relay diagnostics
        </h3>
        <span
          className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
            paused ? 'bg-amber-500/20 text-amber-200' : 'bg-emerald-500/20 text-emerald-200'
          }`}
        >
          {paused ? 'Relay paused' : 'Live relay'}
        </span>
      </div>

      <AgentRelaySyncAlerts alerts={syncAlerts} />

      {reconcile ? (
        <div
          className={`mt-4 rounded-xl border px-4 py-3 text-xs ${
            deltaBad
              ? 'border-red-500/60 bg-red-950/35 text-red-50 ring-2 ring-red-500/30'
              : 'border-emerald-500/30 bg-emerald-950/15 text-emerald-100'
          }`}
        >
          {deltaBad ? (
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-red-300">
              Red alert — exchange desync
            </p>
          ) : null}
          <p className={deltaBad ? 'mt-1' : ''}>
            <strong>Exchange authority:</strong> exchange qty{' '}
            <span className="font-mono">{reconcile.exchangePositionQty.toFixed(5)}</span> BTC · ledger
            qty <span className="font-mono">{reconcile.ledgerOpenQty.toFixed(5)}</span> BTC · Δ{' '}
            <span className="font-mono">
              {delta >= 0 ? '+' : ''}
              {delta.toFixed(5)}
            </span>{' '}
            BTC · {reconcile.openLots} open / {reconcile.pendingLots} pending lots
            {deltaBad ? ' — reconcile healing active' : ' — in sync'}
          </p>
        </div>
      ) : null}

      {limitChain ? (
        <div
          className={`mt-3 rounded-xl border px-4 py-3 text-xs ${
            limitBad
              ? 'border-amber-500/50 bg-amber-950/25 text-amber-100'
              : 'border-zinc-800 bg-black/25 text-zinc-300'
          }`}
        >
          <strong>Limit chain:</strong> configured{' '}
          <span className="font-mono">{limitChain.configuredLimit ?? '—'}</span> · relay{' '}
          <span className="font-mono">{limitChain.relayLimit ?? '—'}</span> · execution{' '}
          <span className="font-mono">
            {limitChain.executionOpen}+{limitChain.executionPending}={limitChain.executionTotal}
          </span>
          {limitBad ? ' — mismatch or over capacity' : ' — aligned'}
        </div>
      ) : null}

      {lifecycle && lifecycle.sampleSize > 0 ? (
        <div
          className={`mt-3 rounded-xl border px-4 py-3 text-xs ${
            lifecycleBad
              ? 'border-amber-500/50 bg-amber-950/25 text-amber-100'
              : 'border-zinc-800 bg-black/25 text-zinc-300'
          }`}
        >
          <strong>Lifecycle integrity:</strong>{' '}
          <span className="font-mono text-sm font-bold">{lifecycle.integrityPct}%</span> (
          {lifecycle.completeCount}/{lifecycle.sampleSize} trades complete ORDER→FILLED→EXIT)
          {lifecycleBad && lifecycle.recentGaps.length > 0
            ? ` — gap on ${lifecycle.recentGaps[0]?.tradeId} missing ${lifecycle.recentGaps[0]?.missingStages.join(', ')}`
            : ''}
        </div>
      ) : null}

      <div className="mt-4">
        <AgentRelayFidelityPanel fidelity={relayFidelity} reconcile={reconcile} />
      </div>

      {agent.walletStatusHint && mode === 'live' ? (
        <p className="mt-3 text-xs text-amber-200/85">{agent.walletStatusHint}</p>
      ) : null}

      <p className="mt-3 text-[11px] text-zinc-600">
        Option B virtual lots · max ${BITFINEX_COPY_DEFAULT_MARGIN_USD} margin/lot · platform cap $
        {DEFAULT_SUBSCRIBER_MAX_MARGIN_USD}/trade · 100x leverage on {exchange} BTC-PERP
      </p>
    </section>
  );
}

export function CopyTradeHub({
  signedIn,
  agent,
  exchangeLabel,
  exchangeProvider,
  hired,
  instanceMode,
  instanceStatus,
  copyRelaySim,
  showcaseAgent,
  activeDesk,
  onSelectDesk,
  onStartRelaySim,
  onStopRelaySim,
  relaySimBusy,
  hireHref,
  slug,
}: {
  slug?: string;
  signedIn: boolean;
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  exchangeProvider?: string | null;
  hired: boolean;
  instanceMode?: 'copy' | 'live' | null;
  instanceStatus?: string | null;
  copyRelaySim?: CopyRelaySimState | null;
  showcaseAgent: TradingAgentSummary;
  activeDesk: AgentDeskId;
  onSelectDesk: (desk: AgentDeskId) => void;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  relaySimBusy?: boolean;
  hireHref: string;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const isLive = hired && instanceMode === 'live';
  const simActive = Boolean(copyRelaySim?.active);
  const canSim = isLive && exchangeProvider === 'bitfinex';

  const deskHint =
    activeDesk === 'showcase'
      ? 'Admin research bot on your home PC — signals and reasoning only, not your copy session.'
      : activeDesk === 'relay-sim'
        ? simActive
          ? 'Paper book with real BTC prices. Live orders blocked while sim runs.'
          : 'Test Option B relay logic before placing real orders.'
        : isLive
          ? instanceStatus === 'PAUSED'
            ? 'Relay paused — open positions stay on your exchange.'
            : 'Real orders on your exchange when showcase signals fire.'
          : `Connect ${exchange} API keys to mirror showcase signals on your account.`;

  return (
    <section className="space-y-3">
      <div className="rounded-xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/20 via-zinc-950/50 to-zinc-950/80 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">
              Real copy trading
            </p>
            <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">
              {isLive
                ? `Your ${exchange} copy session`
                : `Connect ${exchange} — mirror the showcase bot`}
            </h2>
            <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">{deskHint}</p>
          </div>
          {!isLive && signedIn ? (
            <Link
              href={hireHref}
              className="shrink-0 rounded-lg bg-emerald-600 px-5 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Connect {exchange}
            </Link>
          ) : !signedIn ? (
            <Link
              href={`/login?callbackUrl=${encodeURIComponent(hireHref)}`}
              className="shrink-0 rounded-lg bg-emerald-600 px-5 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Sign in
            </Link>
          ) : (
            <div className="shrink-0 text-right">
              <p className="text-[10px] uppercase tracking-widest text-emerald-400">Connected</p>
              <p className="mt-0.5 text-base font-bold text-white">
                {formatUsd(agent.exchangeBalanceUsd ?? 0, 0)}
              </p>
              <p className="text-[10px] text-zinc-500">derivatives</p>
            </div>
          )}
        </div>
      </div>

      <AgentDeskSwitcher
        activeDesk={activeDesk}
        onChange={onSelectDesk}
        exchangeLabel={exchangeLabel}
        liveAvailable={slug === 'conservative-btc' || !slug}
        relaySimAvailable={canSim}
        relaySimActive={simActive}
      />

      <AgentDeskMetricsBar
        activeDesk={activeDesk}
        userAgent={agent}
        showcaseAgent={showcaseAgent}
        copyRelaySim={copyRelaySim}
        exchangeLabel={exchangeLabel}
        isLiveSession={isLive}
        instanceStatus={instanceStatus}
      />

      {canSim && activeDesk === 'relay-sim' ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-500/20 bg-sky-950/10 px-3 py-2">
          {simActive ? (
            <button
              type="button"
              disabled={relaySimBusy}
              onClick={onStopRelaySim}
              className="rounded-lg border border-red-500/50 bg-red-950/40 px-3 py-1.5 text-xs font-semibold text-red-200 disabled:opacity-50"
            >
              {relaySimBusy ? 'Stopping…' : 'Stop relay simulation'}
            </button>
          ) : (
            <button
              type="button"
              disabled={relaySimBusy}
              onClick={onStartRelaySim}
              className="rounded-lg border border-sky-500/50 bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {relaySimBusy ? 'Starting…' : 'Start relay simulation'}
            </button>
          )}
          <p className="text-[11px] text-sky-100/70">
            {simActive
              ? 'Compare sim P&L above vs showcase reference — reconcile before going live.'
              : 'Safe paper test using the same execution path as live copy.'}
          </p>
        </div>
      ) : null}
    </section>
  );
}

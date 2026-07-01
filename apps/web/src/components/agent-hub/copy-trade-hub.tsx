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
import { ShowcaseReferenceBar } from '@/components/agent-hub/showcase-reference-bar';
import {
  AgentRelaySyncAlerts,
  buildRelaySyncAlerts,
} from '@/components/agent-hub/agent-relay-sync-alerts';
import type { TradingAgentSummary } from '@/lib/api';

export function CopyTradeDetailsStrip({
  agent,
  exchangeLabel,
  copyRelayReconcile,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  instanceStatus,
  botConnected,
}: {
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  instanceStatus?: string | null;
  botConnected?: boolean;
}) {
  const reconcile = copyRelayReconcile ?? null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaBad = reconcile?.alert ?? Math.abs(delta) > 0.001;
  const limitChain = copyRelayLimitChain;
  const limitBad = limitChain != null && !limitChain.aligned;
  const lifecycle = tradeLifecycleIntegrity;
  const lifecycleBad = lifecycle != null && lifecycle.integrityPct < 100;
  const paused = instanceStatus === 'PAUSED';
  const exchange = exchangeLabel ?? 'Bitfinex';
  const syncAlerts = buildRelaySyncAlerts({
    mode: 'live',
    botConnected,
    copyRelayReconcile: reconcile,
    copyRelayLimitChain: limitChain,
    tradeLifecycleIntegrity: lifecycle,
  });

  return (
    <section className="rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-950/90 to-emerald-950/10 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-400">
          Live relay diagnostics
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

      {agent.walletStatusHint ? (
        <p className="mt-3 text-xs text-amber-200/85">{agent.walletStatusHint}</p>
      ) : null}

      <p className="mt-3 text-[11px] text-zinc-600">
        Option B virtual lots · max ${BITFINEX_COPY_DEFAULT_MARGIN_USD} margin/lot · platform cap $
        {DEFAULT_SUBSCRIBER_MAX_MARGIN_USD}/trade · 100x leverage on {exchange} BTC-PERP
      </p>
    </section>
  );
}

function deskHeader(
  activeDesk: AgentDeskId,
  exchange: string,
  isLive: boolean,
  instanceStatus: string | null | undefined,
  simActive: boolean,
  botConnected: boolean | undefined,
): { eyebrow: string; title: string; hint: string } {
  if (activeDesk === 'showcase') {
    return {
      eyebrow: 'Showcase bot · live',
      title: 'Conservative BTC Agent · live action',
      hint: botConnected
        ? 'Real-time signals, limit orders, positions, and closed trades from the admin showcase bot on :7002. Observe only — not your exchange.'
        : 'Showcase bot reachable on local network only — public tunnel down. Refresh once the bot + tunnel are back online.',
    };
  }
  if (activeDesk === 'relay-sim') {
    return {
      eyebrow: `${exchange} relay simulation`,
      title: `Your ${exchange} relay sim session`,
      hint: simActive
        ? 'Real Bitfinex API, 1 order at a time · $20 · 100x — test the full order lifecycle, then resume live copy.'
        : 'Connect API once, then start simulation to place a single capped $20 / 100x order and test the API lifecycle.',
    };
  }
  if (isLive) {
    return {
      eyebrow: `${exchange} live copy`,
      title: `Your ${exchange} live session`,
      hint:
        instanceStatus === 'PAUSED'
          ? 'Relay paused — open positions stay on your exchange. Start real trading when ready.'
          : 'Real orders on your exchange when showcase signals fire — your money, your API.',
    };
  }
  return {
    eyebrow: `${exchange} live copy`,
    title: `Connect ${exchange} for live copy`,
    hint: 'Mirror the global showcase bot on your real Bitfinex derivatives account.',
  };
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
  hireHref,
  slug,
  botConnected,
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
  hireHref: string;
  botConnected?: boolean;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const isLive = hired && instanceMode === 'live';
  const simActive = Boolean(copyRelaySim?.active);
  const canSim = isLive && exchangeProvider === 'bitfinex';
  const header = deskHeader(activeDesk, exchange, isLive, instanceStatus, simActive, botConnected);
  const borderAccent =
    activeDesk === 'showcase'
      ? 'border-violet-500/25 from-violet-950/20'
      : activeDesk === 'relay-sim'
        ? 'border-sky-500/25 from-sky-950/20'
        : 'border-emerald-500/25 from-emerald-950/20';
  const eyebrowAccent =
    activeDesk === 'showcase'
      ? 'text-violet-400'
      : activeDesk === 'relay-sim'
        ? 'text-sky-400'
        : 'text-emerald-400';

  return (
    <section className="space-y-3">
      <div
        className={`rounded-xl border bg-gradient-to-br via-zinc-950/50 to-zinc-950/80 p-4 sm:p-5 ${borderAccent}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-xl">
            <p className={`text-[10px] font-bold uppercase tracking-[0.2em] ${eyebrowAccent}`}>
              {header.eyebrow}
            </p>
            <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">{header.title}</h2>
            <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">{header.hint}</p>
          </div>
          {activeDesk === 'live' && !isLive && signedIn ? (
            <Link
              href={hireHref}
              className="shrink-0 rounded-lg bg-emerald-600 px-5 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Connect {exchange}
            </Link>
          ) : activeDesk === 'live' && !signedIn ? (
            <Link
              href={`/login?callbackUrl=${encodeURIComponent(hireHref)}`}
              className="shrink-0 rounded-lg bg-emerald-600 px-5 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Sign in
            </Link>
          ) : activeDesk === 'live' && isLive ? (
            <div className="shrink-0 text-right">
              <p className="text-[10px] uppercase tracking-widest text-emerald-400">Connected</p>
              <p className="mt-0.5 text-base font-bold text-white">
                {formatUsd(agent.exchangeBalanceUsd ?? 0, 0)}
              </p>
              <p className="text-[10px] text-zinc-500">derivatives USDT</p>
            </div>
          ) : activeDesk === 'relay-sim' && isLive ? (
            <div className="shrink-0 text-right">
              <p className="text-[10px] uppercase tracking-widest text-sky-400">API</p>
              <p className="mt-0.5 text-sm font-bold text-white">Connected</p>
              <p className="text-[10px] text-zinc-500">no rental · paper only</p>
            </div>
          ) : activeDesk === 'showcase' ? (
            <div className="shrink-0 text-right">
              <p className="text-[10px] uppercase tracking-widest text-violet-400">Showcase</p>
              <p className="mt-0.5 text-sm font-bold text-white">
                {botConnected ? 'Live · :7002' : 'Tunnel down'}
              </p>
              <p className="text-[10px] text-zinc-500">observe only</p>
            </div>
          ) : null}
        </div>
      </div>

      <AgentDeskSwitcher
        activeDesk={activeDesk}
        onChange={onSelectDesk}
        exchangeLabel={exchangeLabel}
        liveAvailable={slug === 'conservative-btc' || !slug}
        relaySimAvailable={canSim}
        relaySimActive={simActive}
        showcaseAvailable={Boolean(botConnected)}
      />

      {(activeDesk === 'live' || activeDesk === 'relay-sim') && (
        <ShowcaseReferenceBar
          showcaseAgent={showcaseAgent}
          botConnected={botConnected}
          copyRelaySim={copyRelaySim}
        />
      )}

      {/* The conservative-btc showcase desk surfaces cumulative full-session metrics via
          AgentAnalyzerPanel instead of this per-restart equity bar (avoid the $500 reset look). */}
      {!(slug === 'conservative-btc' && activeDesk === 'showcase') && (
        <AgentDeskMetricsBar
          activeDesk={activeDesk}
          userAgent={agent}
          showcaseAgent={showcaseAgent}
          copyRelaySim={copyRelaySim}
          exchangeLabel={exchangeLabel}
          isLiveSession={isLive}
          instanceStatus={instanceStatus}
        />
      )}
    </section>
  );
}

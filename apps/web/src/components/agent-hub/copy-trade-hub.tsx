'use client';

import Link from 'next/link';
import {
  BITFINEX_COPY_DEFAULT_MARGIN_USD,
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  formatPercent,
  formatUsd,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import {
  AgentRelayFidelityPanel,
  type RelayFidelitySnapshot,
} from '@/components/agent-hub/agent-relay-fidelity-panel';
import type { TradingAgentSummary } from '@/lib/api';

type CopyMode = 'connect' | 'live' | 'sim' | 'observe';

function ModeCard({
  active,
  recommended,
  disabled,
  title,
  subtitle,
  badge,
  badgeClass,
  borderClass,
  onClick,
  cta,
  ctaHref,
}: {
  active?: boolean;
  recommended?: boolean;
  disabled?: boolean;
  title: string;
  subtitle: string;
  badge: string;
  badgeClass: string;
  borderClass: string;
  onClick?: () => void;
  cta?: string;
  ctaHref?: string;
}) {
  const inner = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <p className={`text-[10px] font-bold uppercase tracking-[0.2em] ${badgeClass}`}>{badge}</p>
        {recommended && (
          <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[9px] font-bold uppercase text-emerald-300">
            Recommended
          </span>
        )}
        {active && (
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[9px] font-bold uppercase text-white">
            Active
          </span>
        )}
      </div>
      <p className="mt-2 text-base font-bold text-white">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-zinc-500">{subtitle}</p>
      {cta && ctaHref ? (
        <span className="mt-3 inline-block text-xs font-semibold text-violet-300">{cta} →</span>
      ) : null}
    </>
  );

  const className = `rounded-2xl border-2 p-4 text-left transition ${
    disabled
      ? 'cursor-not-allowed border-zinc-800/80 bg-zinc-950/20 opacity-55'
      : active
        ? `${borderClass} ring-2 ring-offset-0 ring-offset-transparent`
        : `${borderClass} hover:brightness-110`
  }`;

  if (ctaHref && !disabled) {
    return (
      <Link href={ctaHref} className={className}>
        {inner}
      </Link>
    );
  }

  return (
    <button type="button" disabled={disabled} onClick={onClick} className={`w-full ${className}`}>
      {inner}
    </button>
  );
}

export function CopyTradeDetailsStrip({
  agent,
  exchangeLabel,
  liveBook,
  copyRelayReconcile,
  copyRelaySim,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relayFidelity,
  instanceStatus,
  mode,
}: {
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  liveBook?: TradingAgentDashboardState['liveBook'] | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relayFidelity?: RelayFidelitySnapshot | null;
  instanceStatus?: string | null;
  mode: 'live' | 'sim' | null;
}) {
  if (!mode) return null;

  const exchange = exchangeLabel ?? 'Bitfinex';
  const pos = liveBook?.positions?.[0];
  const pending = liveBook?.pendingOrders?.length ?? 0;
  const reconcile = copyRelayReconcile ?? copyRelaySim?.reconcile ?? null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaBad = reconcile?.alert ?? Math.abs(delta) > 0.001;
  const limitChain = copyRelayLimitChain;
  const limitBad = limitChain != null && !limitChain.aligned;
  const lifecycle = tradeLifecycleIntegrity;
  const lifecycleBad = lifecycle != null && lifecycle.integrityPct < 100;
  const paused = instanceStatus === 'PAUSED';

  return (
    <section className="rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-950/90 to-emerald-950/10 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold uppercase tracking-widest text-emerald-300">
          Your {exchange} copy — live details
        </h3>
        <span
          className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
            mode === 'sim'
              ? 'bg-sky-500/20 text-sky-200'
              : paused
                ? 'bg-amber-500/20 text-amber-200'
                : 'bg-emerald-500/20 text-emerald-200'
          }`}
        >
          {mode === 'sim' ? 'Relay simulation' : paused ? 'Relay paused' : 'Live relay'}
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-zinc-800 bg-black/25 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Open position</p>
          <p className="mt-1 text-sm font-semibold text-white">
            {pos ? `${pos.side} · ${pos.qty.toFixed(4)} BTC` : agent.openPositionSide ?? 'Flat'}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/25 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Pending limits</p>
          <p className="mt-1 text-sm font-semibold text-white">{pending}</p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/25 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Session P&amp;L</p>
          <p
            className={`mt-1 text-sm font-semibold ${
              (agent.sessionPnlUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
            }`}
          >
            {formatUsd(agent.sessionPnlUsd ?? 0, 2)}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/25 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">
            {mode === 'sim' ? 'Paper balance' : 'Derivatives'}
          </p>
          <p className="mt-1 text-sm font-semibold text-white">
            {formatUsd(
              mode === 'sim'
                ? (copyRelaySim?.ledger?.derivativesUsd ?? 500)
                : (agent.exchangeBalanceUsd ?? agent.balanceUsd ?? 0),
              2,
            )}
          </p>
        </div>
      </div>

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
  const relayOn = isLive && instanceStatus !== 'PAUSED';
  const canSim = isLive && exchangeProvider === 'bitfinex';

  const currentMode: CopyMode = !isLive
    ? 'connect'
    : simActive
      ? 'sim'
      : relayOn
        ? 'live'
        : 'connect';

  const showcasePnl = showcaseAgent.sessionPnlUsd ?? 0;
  const yourPnl = agent.sessionPnlUsd ?? 0;

  return (
    <section className="space-y-5">
      <div className="rounded-2xl border border-emerald-500/35 bg-gradient-to-br from-emerald-950/25 via-zinc-950/50 to-zinc-950/80 p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-emerald-400">
              Real copy trading
            </p>
            <h2 className="mt-1 text-xl font-bold text-white sm:text-2xl">
              Connect {exchange} API — mirror the showcase bot on your account
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              The admin showcase is a public research feed. Authentic copy trading means your{' '}
              <strong className="text-zinc-200">{exchange}</strong> account receives the same signals,
              limits, and Scenario C exits — with full transparency on orders, positions, and P&amp;L below.
            </p>
          </div>
          {!isLive && signedIn ? (
            <Link
              href={hireHref}
              className="shrink-0 rounded-xl bg-emerald-600 px-6 py-3 text-center text-sm font-bold text-white shadow-lg shadow-emerald-900/40 hover:bg-emerald-500"
            >
              Connect {exchange} API
            </Link>
          ) : !signedIn ? (
            <Link
              href={`/login?callbackUrl=${encodeURIComponent(hireHref)}`}
              className="shrink-0 rounded-xl bg-emerald-600 px-6 py-3 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Sign in to connect
            </Link>
          ) : (
            <div className="shrink-0 text-right">
              <p className="text-[10px] uppercase tracking-widest text-emerald-400">Connected</p>
              <p className="mt-1 text-lg font-bold text-white">{formatUsd(agent.exchangeBalanceUsd ?? 0, 0)}</p>
              <p className="text-[11px] text-zinc-500">derivatives available</p>
            </div>
          )}
        </div>

        {isLive && (
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">Showcase P&amp;L</p>
              <p className={`text-sm font-bold ${showcasePnl >= 0 ? 'text-violet-300' : 'text-red-400'}`}>
                {formatUsd(showcasePnl, 2)}
              </p>
            </div>
            <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">Your copy P&amp;L</p>
              <p className={`text-sm font-bold ${yourPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {formatUsd(yourPnl, 2)}
              </p>
            </div>
            <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">Return</p>
              <p className="text-sm font-bold text-white">{formatPercent(agent.netReturnPct ?? 0)}</p>
            </div>
          </div>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <ModeCard
          recommended
          active={currentMode === 'live' || activeDesk === 'live'}
          disabled={!signedIn}
          badge={`${exchange} · live relay`}
          badgeClass="text-emerald-300"
          borderClass={
            activeDesk === 'live'
              ? 'border-emerald-500/70 bg-emerald-950/30 ring-emerald-500/30'
              : 'border-zinc-800 bg-zinc-950/40 border-emerald-500/35'
          }
          title={isLive ? (relayOn ? 'Live copy running' : 'Relay paused') : `Copy on your ${exchange}`}
          subtitle={
            isLive
              ? 'Real orders on your exchange. Every open limit, merged position, and closed leg — same Option B relay as the showcase.'
              : `Connect read+trade API keys. Platform caps $${DEFAULT_SUBSCRIBER_MAX_MARGIN_USD} margin per virtual lot.`
          }
          cta={isLive ? undefined : 'Start setup'}
          ctaHref={isLive ? undefined : hireHref}
          onClick={isLive ? () => onSelectDesk('live') : undefined}
        />

        <ModeCard
          active={currentMode === 'sim' || activeDesk === 'relay-sim'}
          disabled={!canSim}
          badge={`${exchange} · relay sim`}
          badgeClass="text-sky-300"
          borderClass={
            activeDesk === 'relay-sim'
              ? 'border-sky-500/70 bg-sky-950/30 ring-sky-500/30'
              : 'border-zinc-800 bg-zinc-950/40 border-sky-500/35'
          }
          title={simActive ? 'Simulation running' : 'Test relay (no real money)'}
          subtitle={
            canSim
              ? 'Paper book with real BTC prices. Same virtual-lot logic — reconcile ledger vs position before going live.'
              : `Connect ${exchange} first, then run relay simulation beside live copy.`
          }
          onClick={
            canSim
              ? () => {
                  onSelectDesk('relay-sim');
                }
              : undefined
          }
        />

        <ModeCard
          active={activeDesk === 'showcase'}
          badge="Admin showcase"
          badgeClass="text-violet-300"
          borderClass={
            activeDesk === 'showcase'
              ? 'border-violet-500/70 bg-violet-950/35 ring-violet-500/30'
              : 'border-zinc-800 bg-zinc-950/40'
          }
          title="Observe research bot"
          subtitle="Public Railway bot for signals and reasoning — reference only, not your copy session."
          onClick={() => onSelectDesk('showcase')}
        />
      </div>

      {canSim && activeDesk === 'relay-sim' && (
        <div className="flex flex-wrap gap-2 rounded-xl border border-sky-500/25 bg-sky-950/15 px-4 py-3">
          {simActive ? (
            <button
              type="button"
              disabled={relaySimBusy}
              onClick={onStopRelaySim}
              className="rounded-lg border border-red-500/50 bg-red-950/40 px-4 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
            >
              {relaySimBusy ? 'Stopping…' : 'Stop relay simulation'}
            </button>
          ) : (
            <button
              type="button"
              disabled={relaySimBusy}
              onClick={onStartRelaySim}
              className="rounded-lg border border-sky-500/50 bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {relaySimBusy ? 'Starting…' : 'Start relay simulation'}
            </button>
          )}
          <p className="flex-1 text-xs text-sky-100/80 self-center">
            {simActive
              ? 'Live orders blocked while sim runs. Compare showcase vs sim P&L in the desk below.'
              : 'Safe paper test — uses the same execution path as live copy.'}
          </p>
        </div>
      )}
    </section>
  );
}

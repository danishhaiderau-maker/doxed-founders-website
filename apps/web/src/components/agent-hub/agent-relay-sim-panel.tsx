'use client';

import {
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  formatPercent,
  formatUsd,
  type CopyRelayLimitChainSnapshot,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type TradeLifecycleIntegritySnapshot,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentTransparencyTables } from '@/components/agent-hub/agent-transparency-tables';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import {
  AgentRelayFidelityPanel,
  type RelayFidelitySnapshot,
} from '@/components/agent-hub/agent-relay-fidelity-panel';
import {
  AgentRelaySyncAlerts,
  buildRelaySyncAlerts,
} from '@/components/agent-hub/agent-relay-sync-alerts';
import type { TradingAgentActivityEntry, TradingAgentSummary } from '@/lib/api';

const EMPTY_BOOK: TradingAgentDashboardState['liveBook'] = {
  activeSignals: [],
  positions: [],
  pendingOrders: [],
  expiredOrders: [],
  trades: [],
};

function ReconcileMetric({
  label,
  value,
  alert,
}: {
  label: string;
  value: string;
  alert?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border px-3 py-2.5 ${
        alert ? 'border-red-500/50 bg-red-950/25' : 'border-zinc-800 bg-black/20'
      }`}
    >
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${alert ? 'text-red-300' : 'text-white'}`}>
        {value}
      </p>
    </div>
  );
}

export function AgentRelaySimPanel({
  exchangeLabel,
  signedIn,
  showcaseAgent,
  copyRelaySim,
  copyRelayReconcile,
  relayFidelity,
  relaySimLiveBook,
  showcaseLiveBook,
  showcaseActivity: _showcaseActivity,
  simActivity,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  botConnected,
  onStart,
  onStop,
  busy,
  instanceStatus,
  hideSummaryMetrics,
}: {
  signedIn: boolean;
  exchangeLabel?: string | null;
  showcaseAgent: TradingAgentSummary;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  relayFidelity?: RelayFidelitySnapshot | null;
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseActivity: TradingAgentActivityEntry[];
  simActivity: TradingAgentActivityEntry[];
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  botConnected?: boolean;
  onStart?: () => void;
  onStop?: () => void;
  busy?: boolean;
  instanceStatus?: string | null;
  hideSummaryMetrics?: boolean;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const sim = copyRelaySim;
  const active = Boolean(sim?.active);
  const reconcile = active ? (sim?.reconcile ?? null) : (copyRelayReconcile ?? sim?.reconcile ?? null);
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaAlert =
    reconcile?.alert ?? Math.abs(delta) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC;
  const showcasePnl = showcaseAgent.sessionPnlUsd ?? sim?.showcasePnlUsd ?? 0;
  const simPnl = sim?.sessionPnlUsd ?? 0;
  const pnlGap = simPnl - showcasePnl;
  const paperBalance = sim?.ledger?.derivativesUsd ?? 500;
  const simBook = relaySimLiveBook ?? EMPTY_BOOK;
  const showcaseRef = showcaseLiveBook ?? EMPTY_BOOK;
  const simHasRows =
    simBook.activeSignals.length +
      simBook.positions.length +
      simBook.pendingOrders.length +
      simBook.trades.length >
    0;

  const syncAlerts = buildRelaySyncAlerts({
    mode: 'sim',
    botConnected,
    copyRelaySim: sim,
    copyRelayReconcile: reconcile,
    copyRelayLimitChain,
    tradeLifecycleIntegrity,
    relayFidelity,
  });

  return (
    <section className="rounded-2xl border-2 border-sky-500/45 bg-zinc-950/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/80 pb-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-sky-300">
            {exchange} · relay simulation
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Option B paper relay</h2>
          <p className="mt-1 max-w-2xl text-xs text-zinc-500">
            Virtual $20 lots on merged BTC-PERP — same relay logic as live copy, no real exchange
            orders. Compare sim book vs admin showcase below; warnings flag sync drift before you
            go live.
          </p>
        </div>
        {signedIn ? (
          <div className="flex flex-wrap gap-2">
            {active ? (
              <button
                type="button"
                disabled={busy}
                onClick={onStop}
                className="rounded-lg border border-red-500/50 bg-red-950/40 px-4 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
              >
                {busy ? 'Stopping…' : 'Stop relay sim'}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={onStart}
                className="rounded-lg border border-sky-500/50 bg-sky-950/40 px-4 py-2 text-sm font-semibold text-sky-200 disabled:opacity-50"
              >
                {busy ? 'Starting…' : 'Start relay sim'}
              </button>
            )}
          </div>
        ) : null}
      </div>

      <div className="mt-4 space-y-4">
        {!signedIn ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
            Sign in and connect {exchange} (hire live copy) to run relay simulation beside the
            showcase desk.
          </p>
        ) : null}

        {active ? (
          <p className="rounded-lg border border-sky-500/30 bg-sky-950/15 px-3 py-2 text-xs text-sky-100/90">
            Relay sim active — live orders blocked (instance {instanceStatus ?? 'PAUSED'}). Paper
            book mirrors Option B virtual lots; merged-position exits tracked per lot in ledger.
          </p>
        ) : signedIn ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2 text-xs text-zinc-500">
            Start sim to mirror showcase signals on a $500 paper book. Live relay stays paused until
            you stop sim and resume live copy.
          </p>
        ) : null}

        <AgentRelaySyncAlerts alerts={syncAlerts} />

        {!hideSummaryMetrics ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <ReconcileMetric label="Showcase session P&amp;L" value={formatUsd(showcasePnl, 2)} />
            <ReconcileMetric
              label="Sim relay P&amp;L"
              value={formatUsd(simPnl, 2)}
              alert={pnlGap < -20}
            />
            <ReconcileMetric
              label="P&amp;L gap (sim − showcase)"
              value={formatUsd(pnlGap, 2)}
              alert={Math.abs(pnlGap) > 25}
            />
            <ReconcileMetric label="Paper derivatives" value={formatUsd(paperBalance, 2)} />
          </div>
        ) : null}

        {reconcile ? (
          <div className="rounded-xl border border-zinc-800 bg-black/25 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-bold uppercase tracking-widest text-zinc-400">
                Ledger reconcile (sim)
              </h3>
              {deltaAlert ? (
                <span className="rounded-full bg-red-500/20 px-2.5 py-0.5 text-[10px] font-bold uppercase text-red-300">
                  Δ alert
                </span>
              ) : (
                <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[10px] font-bold uppercase text-emerald-300">
                  In sync
                </span>
              )}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <ReconcileMetric
                label="Sim position qty"
                value={`${reconcile.exchangePositionQty.toFixed(5)} BTC`}
              />
              <ReconcileMetric
                label="Ledger open qty"
                value={`${reconcile.ledgerOpenQty.toFixed(5)} BTC`}
              />
              <ReconcileMetric
                label="Delta"
                value={`${delta >= 0 ? '+' : ''}${delta.toFixed(5)} BTC`}
                alert={deltaAlert}
              />
              <ReconcileMetric label="Open lots" value={String(reconcile.openLots)} />
              <ReconcileMetric label="Pending lots" value={String(reconcile.pendingLots)} />
            </div>
            {reconcile.markPrice != null ? (
              <p className="mt-2 text-[10px] text-zinc-600">
                Mark {formatUsd(reconcile.markPrice, 0)} · updated{' '}
                {new Date(reconcile.updatedAt).toLocaleTimeString()}
              </p>
            ) : null}
          </div>
        ) : active ? (
          <p className="text-xs text-zinc-500">Waiting for first reconcile tick…</p>
        ) : null}

        <AgentRelayFidelityPanel fidelity={relayFidelity} reconcile={reconcile} />

        <div>
          <p className="mb-2 text-[10px] font-bold uppercase tracking-[0.15em] text-sky-300">
            Sim paper book — signals · positions · orders · trades
          </p>
          {!active ? (
            <p className="mb-3 text-xs text-zinc-500">
              Start relay simulation to populate the paper book. Tables show structure even when
              flat.
            </p>
          ) : !simHasRows ? (
            <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-xs text-amber-100/90">
              Sim is running but no lots yet — waiting for the next showcase signal. Showcase
              reference below shows what the admin bot is doing now.
            </p>
          ) : null}
          <AgentTransparencyTables liveBook={simBook} maxRows={10} />
          <div className="mt-4">
            <AgentTradeJourney
              activity={simActivity}
              liveBook={simBook}
              layout="horizontal"
              showBalance
              windowMinutes={30}
            />
          </div>
        </div>

        {active ? (
          <div className="rounded-xl border border-violet-500/25 bg-violet-950/10 p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-violet-300">
              Showcase reference · home bot ({formatPercent(showcaseAgent.netReturnPct ?? 0)})
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              Admin research signals — compare entry/exit vs sim book above. Relay sim copies these
              on the next tick when a new intent fires.
            </p>
            <div className="mt-3">
              <AgentTransparencyTables liveBook={showcaseRef} maxRows={6} />
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

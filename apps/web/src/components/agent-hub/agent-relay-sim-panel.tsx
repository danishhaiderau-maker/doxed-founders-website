'use client';

import {
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  formatPercent,
  formatUsd,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentTransparencyTables } from '@/components/agent-hub/agent-transparency-tables';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import type { TradingAgentActivityEntry, TradingAgentSummary } from '@/lib/api';

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
  relaySimLiveBook,
  showcaseActivity,
  simActivity,
  onStart,
  onStop,
  busy,
  instanceStatus,
}: {
  signedIn: boolean;
  exchangeLabel?: string | null;
  showcaseAgent: TradingAgentSummary;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  showcaseActivity: TradingAgentActivityEntry[];
  simActivity: TradingAgentActivityEntry[];
  onStart?: () => void;
  onStop?: () => void;
  busy?: boolean;
  instanceStatus?: string | null;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const sim = copyRelaySim;
  const active = Boolean(sim?.active);
  const reconcile = copyRelayReconcile ?? sim?.reconcile ?? null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaAlert =
    reconcile?.alert ?? Math.abs(delta) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC;
  const showcasePnl = showcaseAgent.sessionPnlUsd ?? sim?.showcasePnlUsd ?? 0;
  const simPnl = sim?.sessionPnlUsd ?? 0;
  const pnlGap = simPnl - showcasePnl;
  const paperBalance = sim?.ledger?.derivativesUsd ?? 500;

  return (
    <section className="rounded-2xl border-2 border-sky-500/45 bg-zinc-950/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/80 pb-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-sky-300">
            {exchange} · relay simulation
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Option B paper relay</h2>
          <p className="mt-1 max-w-2xl text-xs text-zinc-500">
            Real BTC mark prices and the same virtual-lot relay logic as live copy — no exchange
            orders, no real money. Compare showcase P&amp;L vs sim performance and watch ledger ↔
            position sync.
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
            book mirrors Option B: $20/lot virtual ledger on merged BTC-PERP.
          </p>
        ) : signedIn ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2 text-xs text-zinc-500">
            Start sim to mirror showcase signals on a $500 paper book. Live relay stays paused until
            you stop sim and resume live copy.
          </p>
        ) : null}

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

        {reconcile ? (
          <div className="rounded-xl border border-zinc-800 bg-black/25 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-bold uppercase tracking-widest text-zinc-400">
                Ledger reconcile
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
                label="Exchange position (sim)"
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

        {relaySimLiveBook ? (
          <>
            <AgentTransparencyTables liveBook={relaySimLiveBook} maxRows={10} />
            <AgentTradeJourney
              activity={simActivity}
              liveBook={relaySimLiveBook}
              layout="horizontal"
              showBalance
              windowMinutes={30}
            />
          </>
        ) : active ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
            Sim book loading — relay tick runs every few seconds.
          </p>
        ) : null}

        {active && showcaseActivity.length > 0 ? (
          <div className="rounded-xl border border-violet-500/25 bg-violet-950/10 p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-violet-300">
              Showcase reference ({formatPercent(showcaseAgent.netReturnPct ?? 0)})
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              Admin bot trades for the same window — use P&amp;L gap above to spot relay drift.
            </p>
          </div>
        ) : null}
      </div>
    </section>
  );
}

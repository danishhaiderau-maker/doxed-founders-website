'use client';

import {
  COPY_RELAY_SIM_RECONCILE_ALERT_BTC,
  formatUsd,
  type CopyRelayLimitChainSnapshot,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type TradeLifecycleIntegritySnapshot,
  type RelaySimParticipantStats,
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
import { ShowcaseSyncPanel } from '@/components/agent-hub/showcase-sync-panel';
import { RelaySimLiveViewToggle } from '@/components/agent-hub/relay-sim-live-view-toggle';
import type { TradingAgentActivityEntry } from '@/lib/api';
import { downloadRelaySimAudit } from '@/lib/api';
import { CollapsibleInfo } from '@/components/ui/collapsible-info';

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
  copyRelaySim,
  copyRelayReconcile: _copyRelayReconcile,
  relayFidelity,
  relaySimLiveBook,
  simActivity,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relaySimParticipantStats,
  botConnected,
  onStart,
  onStop,
  onReset,
  slug,
  accessToken,
  busy,
  instanceStatus,
  hideSummaryMetrics,
  relaySimLiveView,
  onRelaySimLiveViewChange,
}: {
  signedIn: boolean;
  exchangeLabel?: string | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  relayFidelity?: RelayFidelitySnapshot | null;
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  simActivity: TradingAgentActivityEntry[];
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relaySimParticipantStats?: RelaySimParticipantStats | null;
  botConnected?: boolean;
  onStart?: () => void;
  onStop?: () => void;
  onReset?: () => void;
  slug?: string;
  accessToken?: string | null;
  busy?: boolean;
  instanceStatus?: string | null;
  hideSummaryMetrics?: boolean;
  relaySimLiveView?: boolean;
  onRelaySimLiveViewChange?: (enabled: boolean) => void;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  const sim = copyRelaySim;
  const active = Boolean(sim?.active);
  const reconcile = active ? (sim?.reconcile ?? null) : null;
  const delta = reconcile?.deltaBtc ?? 0;
  const deltaAlert =
    reconcile?.alert ?? Math.abs(delta) > COPY_RELAY_SIM_RECONCILE_ALERT_BTC;
  const simPnl = sim?.sessionPnlUsd ?? 0;
  const paperBalance = sim?.ledger?.derivativesUsd ?? 500;
  const simBook = relaySimLiveBook ?? EMPTY_BOOK;
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
    relaySimParticipantStats,
    relayFidelity,
  });

  return (
    <section className="rounded-2xl border-2 border-sky-500/45 bg-zinc-950/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/80 pb-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-sky-300">
            {exchange} · relay simulation
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Bitfinex API relay test</h2>
          <p className="mt-1 max-w-2xl text-xs text-zinc-500">
            Real Bitfinex API orders, tightly capped — 1 position at a time · $20 margin · 100x
            leverage. Showcase signals come from global bot :7002 (not local lab :7800).
          </p>
        </div>
        {signedIn ? (
          <div className="flex flex-wrap gap-2">
            {active ? (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={onReset}
                  className="rounded-lg border border-amber-500/50 bg-amber-950/40 px-4 py-2 text-sm font-semibold text-amber-200 disabled:opacity-50"
                >
                  {busy ? '…' : 'Reset ledger ($500)'}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={onStop}
                  className="rounded-lg border border-red-500/50 bg-red-950/40 px-4 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
                >
                  {busy ? 'Stopping…' : 'Stop simulation'}
                </button>
              </>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={onStart}
                className="rounded-lg border border-sky-500/50 bg-sky-950/40 px-4 py-2 text-sm font-semibold text-sky-200 disabled:opacity-50"
              >
                {busy ? 'Starting…' : 'Start simulation trading'}
              </button>
            )}
            {slug && accessToken ? (
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  try {
                    const { blob, filename } = await downloadRelaySimAudit(slug, accessToken);
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    alert(e instanceof Error ? e.message : 'Export failed');
                  }
                }}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm font-semibold text-zinc-200 disabled:opacity-50"
              >
                Download sync audit CSV
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-4 space-y-4">
        {/* Why relay sim exists — tucked into a collapsible so it stays out of the
            way once you have read it. Tap to expand. */}
        <CollapsibleInfo
          title="Why this tab exists"
          hint="live-API dress rehearsal - tap to read"
          accent="blue"
        >
          <p>
            Relay sim is the live-API dress rehearsal before real trading. It places a{' '}
            <strong className="text-white">single $20 order at 100x leverage</strong> on your real
            Bitfinex account - one order at a time, no stacking - so you can watch a complete trade
            lifecycle (entry to fill to manage to exit) and confirm the exchange API can place,
            cancel, and fill orders correctly.
          </p>
          <p className="text-sky-200/80">
            Once you have seen a full lifecycle and are confident the API pipeline works, stop sim
            and resume live copy for real trading. This replaces the old copy-trade path, which is
            retired.
          </p>
        </CollapsibleInfo>

        {!signedIn ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-4 text-sm text-zinc-500">
            Sign in and connect {exchange} (hire live copy) to run relay simulation beside the
            showcase desk.
          </p>
        ) : null}

        {active ? (
          <p className="rounded-lg border border-sky-500/30 bg-sky-950/15 px-3 py-2 text-xs text-sky-100/90">
            Relay sim active — real Bitfinex API testing mode (instance{' '}
            {instanceStatus ?? 'PAUSED'}). Capped at 1 order · $20 · 100x. The next showcase signal
            opens one real position; no new entries until it closes.
          </p>
        ) : signedIn ? (
          <p className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2 text-xs text-zinc-500">
            Start sim to place a single capped $20 / 100x order on the real Bitfinex API and watch
            the full lifecycle. Live relay stays paused while sim runs; stop sim to resume real
            trading.
          </p>
        ) : null}

        {active && onRelaySimLiveViewChange ? (
          <RelaySimLiveViewToggle
            simActive={active}
            enabled={Boolean(relaySimLiveView)}
            onChange={onRelaySimLiveViewChange}
          />
        ) : null}

        <AgentRelaySyncAlerts alerts={syncAlerts} />

        {!hideSummaryMetrics ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <ReconcileMetric label="Sim session P&amp;L" value={formatUsd(simPnl, 2)} />
            <ReconcileMetric label="Paper derivatives" value={formatUsd(paperBalance, 2)} />
            <ReconcileMetric
              label="Sim status"
              value={active ? 'Running' : 'Stopped'}
            />
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

        <ShowcaseSyncPanel
          input={{
            botConnected,
            reconcile,
            fidelity: relayFidelity ?? undefined,
            lifecycle: tradeLifecycleIntegrity ?? undefined,
          }}
          mode="sim"
          simActive={active}
          onAutoStop={onStop}
          autoStopBusy={busy}
        />

        <AgentRelayFidelityPanel fidelity={relayFidelity} reconcile={reconcile} />

        <div>
          <p className="mb-2 text-[10px] font-bold uppercase tracking-[0.15em] text-sky-300">
            Bitfinex API — signals · positions · orders · trades
          </p>
          {!active ? (
            <p className="mb-3 text-xs text-zinc-500">
              Start relay sim to place a single capped $20 / 100x order on the real Bitfinex API.
              Tables show structure even when flat.
            </p>
          ) : !simHasRows ? (
            <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-xs text-amber-100/90">
              Sim is running but no order yet — waiting for the next showcase signal from :7002. One
              real $20 / 100x order will be placed; no new entries until it closes.
            </p>
          ) : null}
          <AgentTransparencyTables liveBook={simBook} maxRows={5} />
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
      </div>
    </section>
  );
}

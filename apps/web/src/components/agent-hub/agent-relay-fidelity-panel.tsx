'use client';

import { formatUsd, type CopyRelayReconcileSnapshot } from '@dcf/utils';

export type RelayFidelityRow = {
  tradeId: string;
  cycleId: string;
  direction: string | null;
  showcaseEntry: number | null;
  bitfinexEntry: number | null;
  entryDeltaUsd: number | null;
  entryDeltaPct: number | null;
  showcaseExit: number | null;
  bitfinexExit: number | null;
  exitDeltaUsd: number | null;
  exitDeltaPct: number | null;
  showcaseExitReason: string | null;
  relayExitReason: string | null;
  closedAt: string | null;
};

export type RelayFidelitySnapshot = {
  rows: RelayFidelityRow[];
  summary: {
    tradeCount: number;
    avgEntryDeltaPct: number | null;
    avgExitDeltaPct: number | null;
    maxEntryDeltaPct: number | null;
    maxExitDeltaPct: number | null;
  };
  policy: {
    showcaseMirrorOnly: boolean;
    copyPolicyVersion: number;
    executionPollMs: number;
    signalPollMs: number;
  };
};

function deltaClass(pct: number | null, warn = 0.05) {
  if (pct == null) return 'text-zinc-500';
  if (Math.abs(pct) <= warn) return 'text-emerald-400';
  if (Math.abs(pct) <= 0.15) return 'text-amber-300';
  return 'text-red-400';
}

function fmtPrice(v: number | null) {
  return v != null && Number.isFinite(v) ? formatUsd(v, 0) : '—';
}

function fmtPct(v: number | null) {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(3)}%`;
}

export function AgentRelayFidelityPanel({
  fidelity,
  reconcile,
}: {
  fidelity?: RelayFidelitySnapshot | null;
  reconcile?: CopyRelayReconcileSnapshot | null;
}) {
  if (!fidelity && !reconcile) return null;

  const delta = reconcile?.deltaBtc ?? 0;
  const deltaAlert = reconcile?.alert ?? Math.abs(delta) > 0.001;

  return (
    <section className="rounded-xl border border-violet-500/30 bg-violet-950/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
            Relay fidelity
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            Showcase vs Bitfinex entry/exit — truth meter for move-by-move copy.
          </p>
        </div>
        {fidelity?.policy ? (
          <div className="text-right text-[10px] text-zinc-600">
            <p>Mirror-only: {fidelity.policy.showcaseMirrorOnly ? 'ON' : 'OFF'}</p>
            <p>
              Poll {fidelity.policy.executionPollMs}ms / policy v{fidelity.policy.copyPolicyVersion}
            </p>
          </div>
        ) : null}
      </div>

      {reconcile ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <div className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2">
            <p className="text-[10px] uppercase text-zinc-500">Exchange qty</p>
            <p className="text-sm font-semibold text-white">
              {reconcile.exchangePositionQty.toFixed(5)} BTC
            </p>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2">
            <p className="text-[10px] uppercase text-zinc-500">Ledger qty</p>
            <p className="text-sm font-semibold text-white">
              {reconcile.ledgerOpenQty.toFixed(5)} BTC
            </p>
          </div>
          <div
            className={`rounded-lg border px-3 py-2 ${
              deltaAlert ? 'border-red-500/40 bg-red-950/20' : 'border-emerald-500/30 bg-emerald-950/10'
            }`}
          >
            <p className="text-[10px] uppercase text-zinc-500">Delta</p>
            <p className={`text-sm font-semibold ${deltaAlert ? 'text-red-300' : 'text-emerald-300'}`}>
              {delta >= 0 ? '+' : ''}
              {delta.toFixed(5)} BTC
            </p>
          </div>
        </div>
      ) : null}

      {fidelity?.summary ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-4">
          <Metric label="Trades" value={String(fidelity.summary.tradeCount)} />
          <Metric
            label="Avg entry Δ"
            value={fmtPct(fidelity.summary.avgEntryDeltaPct)}
            accent={deltaClass(fidelity.summary.avgEntryDeltaPct)}
          />
          <Metric
            label="Avg exit Δ"
            value={fmtPct(fidelity.summary.avgExitDeltaPct)}
            accent={deltaClass(fidelity.summary.avgExitDeltaPct)}
          />
          <Metric
            label="Max entry Δ"
            value={fmtPct(fidelity.summary.maxEntryDeltaPct)}
            accent={deltaClass(fidelity.summary.maxEntryDeltaPct)}
          />
        </div>
      ) : null}

      {fidelity?.rows?.length ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-[10px] uppercase tracking-wider text-zinc-500">
                <th className="py-2 pr-3">Trade</th>
                <th className="py-2 pr-3">Showcase entry</th>
                <th className="py-2 pr-3">Bitfinex entry</th>
                <th className="py-2 pr-3">Entry Δ</th>
                <th className="py-2 pr-3">Showcase exit</th>
                <th className="py-2 pr-3">Bitfinex exit</th>
                <th className="py-2 pr-3">Exit Δ</th>
                <th className="py-2">Exit reason</th>
              </tr>
            </thead>
            <tbody>
              {fidelity.rows.map((row) => (
                <tr key={row.cycleId} className="border-b border-zinc-900/80 text-zinc-300">
                  <td className="py-2 pr-3 font-mono text-[10px]">
                    {row.tradeId.slice(0, 12)}
                    {row.direction ? ` · ${row.direction}` : ''}
                  </td>
                  <td className="py-2 pr-3">{fmtPrice(row.showcaseEntry)}</td>
                  <td className="py-2 pr-3">{fmtPrice(row.bitfinexEntry)}</td>
                  <td className={`py-2 pr-3 ${deltaClass(row.entryDeltaPct)}`}>
                    {fmtPct(row.entryDeltaPct)}
                  </td>
                  <td className="py-2 pr-3">{fmtPrice(row.showcaseExit)}</td>
                  <td className="py-2 pr-3">{fmtPrice(row.bitfinexExit)}</td>
                  <td className={`py-2 pr-3 ${deltaClass(row.exitDeltaPct)}`}>
                    {fmtPct(row.exitDeltaPct)}
                  </td>
                  <td className="py-2 text-[10px] text-zinc-500">
                    {row.showcaseExitReason ?? '—'}
                    {row.relayExitReason && row.relayExitReason !== row.showcaseExitReason
                      ? ` → ${row.relayExitReason}`
                      : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-4 text-xs text-zinc-600">No closed relay trades yet — fidelity fills after first mirrored round-trip.</p>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  accent = 'text-white',
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-black/20 px-3 py-2">
      <p className="text-[10px] uppercase text-zinc-500">{label}</p>
      <p className={`text-sm font-semibold ${accent}`}>{value}</p>
    </div>
  );
}

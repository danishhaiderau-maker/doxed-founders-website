'use client';

import { formatMelbourneDateTime, formatUsd, type CopyRelayReconcileSnapshot } from '@dcf/utils';

export type RelayFidelityRow = {
  tradeId: string;
  localBotTradeId: string | null;
  matchKind: string;
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
  localBotEntryAt: string | null;
  localBotExitAt: string | null;
  relayEntryAt: string | null;
  relayExitAt: string | null;
  entryLagSec: number | null;
  exitLagSec: number | null;
  closedAt: string | null;
};

export type RelayFidelityOrphan = {
  tradeId: string;
  kind: 'relay_without_showcase' | 'showcase_without_relay';
  detail: string;
};

export type RelayFidelitySnapshot = {
  rows: RelayFidelityRow[];
  summary: {
    tradeCount: number;
    avgEntryDeltaPct: number | null;
    avgExitDeltaPct: number | null;
    maxEntryDeltaPct: number | null;
    maxExitDeltaPct: number | null;
    missingShowcaseEntryCount?: number;
    missingShowcaseExitCount?: number;
    avgEntryLagSec?: number | null;
    avgExitLagSec?: number | null;
    unmatchedRelayCount?: number;
    unmatchedShowcaseCount?: number;
  };
  audit?: {
    orphans: RelayFidelityOrphan[];
    relayTradeIds: string[];
    matchedShowcaseTradeIds: string[];
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

function fmtLag(sec: number | null) {
  if (sec == null || !Number.isFinite(sec)) return '—';
  if (Math.abs(sec) < 60) return `${sec >= 0 ? '+' : ''}${sec}s`;
  const m = Math.floor(Math.abs(sec) / 60);
  const s = Math.abs(sec) % 60;
  return `${sec >= 0 ? '+' : '-'}${m}m ${s}s`;
}

function fmtMelb(iso: string | null) {
  return iso ? formatMelbourneDateTime(iso) : '—';
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
  const latestRows = fidelity?.rows?.slice(0, 3) ?? [];

  return (
    <section className="rounded-xl border border-violet-500/30 bg-violet-950/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
            Relay fidelity
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            Same trade ID on global showcase bot :7002 and Bitfinex relay — entry/exit prices, Melbourne
            times, and lag seconds for copy fidelity.
          </p>
        </div>
        {fidelity?.summary &&
        (fidelity.summary.missingShowcaseEntryCount ?? 0) +
          (fidelity.summary.missingShowcaseExitCount ?? 0) >
          0 ? (
          <span className="rounded-full bg-amber-500/20 px-2.5 py-0.5 text-[9px] font-bold uppercase text-amber-200">
            Local bot gaps
          </span>
        ) : null}
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
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Trades matched" value={String(fidelity.summary.tradeCount)} />
          <Metric
            label="Local bot gaps"
            value={`${(fidelity.summary.missingShowcaseEntryCount ?? 0) + (fidelity.summary.missingShowcaseExitCount ?? 0)} missing`}
            accent={
              (fidelity.summary.missingShowcaseEntryCount ?? 0) +
                (fidelity.summary.missingShowcaseExitCount ?? 0) >
              0
                ? 'text-amber-300'
                : 'text-emerald-400'
            }
          />
          <Metric
            label="Avg entry lag"
            value={fmtLag(fidelity.summary.avgEntryLagSec ?? null)}
            accent={
              fidelity.summary.avgEntryLagSec != null && Math.abs(fidelity.summary.avgEntryLagSec) > 30
                ? 'text-amber-300'
                : 'text-emerald-400'
            }
          />
          <Metric
            label="ID orphans"
            value={String(
              (fidelity.summary.unmatchedRelayCount ?? 0) +
                (fidelity.summary.unmatchedShowcaseCount ?? 0),
            )}
            accent={
              (fidelity.summary.unmatchedRelayCount ?? 0) +
                (fidelity.summary.unmatchedShowcaseCount ?? 0) >
              0
                ? 'text-amber-300'
                : 'text-emerald-400'
            }
          />
        </div>
      ) : null}

      {fidelity?.audit?.orphans?.length ? (
        <details className="mt-3 rounded-lg border border-zinc-800/60 bg-black/20 px-3 py-2 text-[11px] text-zinc-400">
          <summary className="cursor-pointer font-semibold uppercase tracking-wider text-zinc-500">
            Relay sync notes ({fidelity.audit.orphans.length})
          </summary>
          <ul className="mt-1 space-y-1 text-zinc-500">
            {fidelity.audit.orphans.slice(0, 5).map((o) => (
              <li key={`${o.kind}-${o.tradeId}`}>
                <span className="font-mono text-[10px]">{o.tradeId}</span> — {o.detail}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[10px] text-zinc-600">
            Orphan trade IDs are usually relay sim offline windows — not counted against sync score.
          </p>
        </details>
      ) : null}

      {latestRows.length ? (
        <div className="mt-4 overflow-x-auto">
          <p className="mb-2 text-[10px] text-zinc-600">
            Last {latestRows.length} trades (Melbourne 24h) — trade ID must match global showcase bot :7002
          </p>
          <table className="w-full min-w-[960px] text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-[10px] uppercase tracking-wider text-zinc-500">
                <th className="py-2 pr-3">Trade ID</th>
                <th className="py-2 pr-3">Local bot entry</th>
                <th className="py-2 pr-3">Local entry time</th>
                <th className="py-2 pr-3">Relay entry</th>
                <th className="py-2 pr-3">Relay entry time</th>
                <th className="py-2 pr-3">Entry Δ / lag</th>
                <th className="py-2 pr-3">Local bot exit</th>
                <th className="py-2 pr-3">Local exit time</th>
                <th className="py-2 pr-3">Relay exit</th>
                <th className="py-2 pr-3">Exit Δ / lag</th>
              </tr>
            </thead>
            <tbody>
              {latestRows.map((row) => (
                <tr key={row.cycleId} className="border-b border-zinc-900/80 text-zinc-300">
                  <td className="py-2 pr-3 font-mono text-[10px]">
                    <div title={row.tradeId}>{row.localBotTradeId ?? row.tradeId}</div>
                    {row.localBotTradeId && row.localBotTradeId !== row.tradeId ? (
                      <div className="text-[9px] text-amber-400">relay: {row.tradeId.slice(0, 14)}…</div>
                    ) : null}
                    {row.matchKind !== 'exact' && row.matchKind !== 'none' ? (
                      <div className="text-[9px] text-zinc-500">match: {row.matchKind}</div>
                    ) : null}
                    {row.direction ? (
                      <div className="text-[9px] text-zinc-500">{row.direction}</div>
                    ) : null}
                  </td>
                  <td className="py-2 pr-3">{fmtPrice(row.showcaseEntry)}</td>
                  <td className="py-2 pr-3 text-[10px]">{fmtMelb(row.localBotEntryAt)}</td>
                  <td className="py-2 pr-3">{fmtPrice(row.bitfinexEntry)}</td>
                  <td className="py-2 pr-3 text-[10px]">{fmtMelb(row.relayEntryAt)}</td>
                  <td className={`py-2 pr-3 ${deltaClass(row.entryDeltaPct)}`}>
                    {fmtPct(row.entryDeltaPct)}
                    <div className="text-[10px] text-zinc-500">{fmtLag(row.entryLagSec)}</div>
                  </td>
                  <td className="py-2 pr-3">{fmtPrice(row.showcaseExit)}</td>
                  <td className="py-2 pr-3 text-[10px]">{fmtMelb(row.localBotExitAt)}</td>
                  <td className="py-2 pr-3">{fmtPrice(row.bitfinexExit)}</td>
                  <td className={`py-2 pr-3 ${deltaClass(row.exitDeltaPct)}`}>
                    {fmtPct(row.exitDeltaPct)}
                    <div className="text-[10px] text-zinc-500">{fmtLag(row.exitLagSec)}</div>
                    <div className="text-[10px] text-zinc-600">
                      {row.showcaseExitReason ?? '—'}
                      {row.relayExitReason && row.relayExitReason !== row.showcaseExitReason
                        ? ` → ${row.relayExitReason}`
                        : ''}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-4 text-xs text-zinc-600">
          No closed relay trades yet — fidelity fills after first mirrored round-trip with matching
          trade ID.
        </p>
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

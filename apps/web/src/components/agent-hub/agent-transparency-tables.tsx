'use client';

import { formatUsd, type TradingAgentDashboardState } from '@dcf/utils';

const MAX_ROWS = 5;

function MiniTable({
  title,
  subtitle,
  headers,
  rows,
  emptyMessage,
}: {
  title: string;
  subtitle?: string;
  headers: string[];
  rows: string[][];
  emptyMessage: string;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-zinc-950/50">
      <div className="border-b border-zinc-800/80 px-4 py-3">
        <h3 className="text-xs font-bold uppercase tracking-widest text-zinc-300">{title}</h3>
        {subtitle && <p className="mt-0.5 text-[10px] text-zinc-500">{subtitle}</p>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-[11px]">
          <thead>
            <tr className="border-b border-zinc-800/60 text-[10px] uppercase tracking-wide text-zinc-500">
              {headers.map((h) => (
                <th key={h} className="whitespace-nowrap px-3 py-2 font-semibold">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={headers.length} className="px-3 py-6 text-center text-zinc-600">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((cells, i) => (
                <tr key={i} className="border-b border-zinc-900/80 text-zinc-300 last:border-0">
                  {cells.map((cell, j) => (
                    <td key={j} className="whitespace-nowrap px-3 py-2.5">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {rows.length > 0 && (
        <p className="border-t border-zinc-800/60 px-3 py-1.5 text-[10px] text-zinc-600">
          Showing latest {Math.min(rows.length, MAX_ROWS)} entries
        </p>
      )}
    </section>
  );
}

function fmtPrice(n: number): string {
  if (!n || !Number.isFinite(n)) return '—';
  return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 1 }) : n.toFixed(2);
}

const EMPTY_BOOK: TradingAgentDashboardState['liveBook'] = {
  activeSignals: [],
  positions: [],
  pendingOrders: [],
  expiredOrders: [],
  trades: [],
};

export function AgentTransparencyTables({
  liveBook,
}: {
  liveBook?: TradingAgentDashboardState['liveBook'];
}) {
  const book = liveBook ?? EMPTY_BOOK;

  const signalRows = book.activeSignals.slice(0, MAX_ROWS).map((s) => [
    s.time,
    s.direction,
    `${s.confidence}%`,
    s.regime,
    s.strategy,
    s.trigger,
    `${s.pullRequiredPct.toFixed(2)}%`,
    fmtPrice(s.signalPrice),
    `${s.maxPullPct.toFixed(2)}%`,
    s.outcome,
    s.fillPrice != null ? fmtPrice(s.fillPrice) : '—',
    s.exitReason ?? '—',
  ]);

  const positionRows = book.positions.slice(0, MAX_ROWS).map((p) => [
    p.leg,
    p.side,
    p.qty.toFixed(4),
    fmtPrice(p.entry),
    fmtPrice(p.current),
    fmtPrice(p.stopLoss),
    fmtPrice(p.takeProfit),
    formatUsd(p.pnlUsd),
  ]);

  const pendingRows = book.pendingOrders.slice(0, MAX_ROWS).map((o) => [
    String(o.ageMin),
    o.side,
    o.status,
    o.qty.toFixed(4),
    fmtPrice(o.limitPrice),
    fmtPrice(o.signalPrice),
  ]);

  const expiredRows = book.expiredOrders.slice(0, MAX_ROWS).map((o) => [
    o.time,
    o.direction,
    fmtPrice(o.limitPrice),
    String(o.ageMin),
    o.reason,
    `${o.confidence}%`,
    o.mode,
  ]);

  const tradeRows = book.trades.slice(0, MAX_ROWS).map((t) => [
    t.time,
    t.tradeId.slice(0, 8),
    t.direction,
    fmtPrice(t.entry),
    fmtPrice(t.exit),
    String(t.durationMin),
    `${t.pnlPct >= 0 ? '+' : ''}${t.pnlPct.toFixed(2)}%`,
    formatUsd(t.netUsd),
    formatUsd(t.grossUsd),
    formatUsd(t.tradeFeesUsd),
    formatUsd(t.fundingUsd),
    t.aiBand,
  ]);

  return (
    <div className="space-y-4">
      <MiniTable
        title="Active signals"
        subtitle="Latest pipeline signals — max 5 rolling entries"
        headers={[
          'Time',
          'Dir (final)',
          'Conf',
          'Regime',
          'Strategy',
          'Trigger',
          'Pull req',
          'Signal price',
          'Max pull',
          'Outcome',
          'Fill price',
          'Exit reason',
        ]}
        rows={signalRows}
        emptyMessage="No active signals right now."
      />
      <MiniTable
        title="Positions"
        headers={['Leg', 'Side', 'Qty', 'Entry', 'Current', 'SL', 'TP', 'PnL']}
        rows={positionRows}
        emptyMessage="No open positions."
      />
      <MiniTable
        title="Pending orders"
        subtitle="Limit orders waiting for fill"
        headers={['Age min', 'Side', 'Status', 'Qty', 'Limit price', 'Signal price']}
        rows={pendingRows}
        emptyMessage="No pending limit orders."
      />
      <MiniTable
        title="Expired orders"
        headers={['Time', 'Dir', 'Limit price', 'Age min', 'Reason', 'Conf', 'Mode']}
        rows={expiredRows}
        emptyMessage="No recently expired orders."
      />
      <MiniTable
        title="Trades"
        subtitle="Closed session trades — newest first"
        headers={[
          'Time',
          'ID',
          'Dir (final)',
          'Entry',
          'Exit',
          'Duration min',
          'PnL %',
          'Net USD',
          'Gross USD',
          'Trade fees',
          'Funding',
          'AI band',
        ]}
        rows={tradeRows}
        emptyMessage="No completed trades in this session yet."
      />
    </div>
  );
}

'use client';

import { displayMelbourneTime, formatUsd, type TradingAgentDashboardState } from '@dcf/utils';

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
          Showing latest {rows.length} entries
        </p>
      )}
    </section>
  );
}

function fmtPrice(n: number): string {
  if (!n || !Number.isFinite(n)) return '—';
  return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 1 }) : n.toFixed(2);
}

export const EMPTY_LIVE_BOOK: TradingAgentDashboardState['liveBook'] = {
  activeSignals: [],
  positions: [],
  pendingOrders: [],
  expiredOrders: [],
  trades: [],
};

export function AgentTransparencyTables({
  liveBook,
  maxRows = 5,
  executionOnly = false,
}: {
  liveBook?: TradingAgentDashboardState['liveBook'];
  maxRows?: number;
  /** Public showcase — positions, orders, trades only (no signals / AI columns). */
  executionOnly?: boolean;
}) {
  const book = liveBook ?? EMPTY_LIVE_BOOK;
  const cap = Math.max(1, Math.min(maxRows, 20));

  const signalRows = book.activeSignals.slice(0, cap).map((s) => [
    displayMelbourneTime(s.time),
    s.direction,
    s.confidence > 0 ? `${s.confidence}%` : 'N/A',
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

  const actualExchangePositions = book.positions.filter(
    (position) =>
      position.leg === 'Exchange net (actual)' ||
      position.leg === 'Bitfinex net',
  );
  const visiblePositions = executionOnly ? actualExchangePositions : book.positions;
  const positionRows = visiblePositions.slice(0, cap).map((p) => [
    p.leg,
    p.side,
    p.qty.toFixed(4),
    fmtPrice(p.entry),
    fmtPrice(p.current),
    fmtPrice(p.stopLoss),
    fmtPrice(p.takeProfit),
    formatUsd(p.pnlUsd),
  ]);
  const hasExchangeNet = book.positions.some(
    (position) =>
      position.leg === 'Exchange net (actual)' ||
      position.leg === 'Bitfinex net',
  );
  const positionSubtitle = hasExchangeNet
    ? 'Exchange net is the one real Bitfinex position. Tracked lots are virtual showcase allocations for independent exits — do not add their P&L to exchange P&L.'
    : undefined;

  const pendingRows = book.pendingOrders.slice(0, cap).map((o) =>
    executionOnly
      ? [
          o.tradeId ?? 'Exchange order',
          o.side,
          o.status === 'ACTIVE' ? 'RESTING' : o.status,
          o.qty.toFixed(5),
          fmtPrice(o.limitPrice),
          String(o.ageMin),
        ]
      : [
          String(o.ageMin),
          o.side,
          o.status,
          o.qty.toFixed(4),
          fmtPrice(o.limitPrice),
          fmtPrice(o.signalPrice),
        ],
  );

  const expiredRows = book.expiredOrders.slice(0, cap).map((o) => [
    displayMelbourneTime(o.createdTime ?? o.time),
    displayMelbourneTime(o.expiredTime ?? o.time),
    o.direction,
    fmtPrice(o.limitPrice),
    String(o.ageMin),
    o.reason,
    `${o.confidence}%`,
    o.mode,
  ]);

  const tradeRows = book.trades.slice(0, cap).map((t) => {
    // pnlPct defaults to 0 when the close path didn't record pnl_margin_pct
    // (already-flat / immediate-flat reconciles). Showing "+0.00%" for a trade
    // that actually lost/won cash is misleading — render "—" when the pct is 0
    // but real cash P&L (netUsd) is non-zero, so the Net USD column stays truthful.
    const pnlPctCell =
      t.pnlPct === 0 && t.netUsd !== 0
        ? '—'
        : `${t.pnlPct >= 0 ? '+' : ''}${t.pnlPct.toFixed(2)}%`;
    return [
      displayMelbourneTime(t.time),
      t.tradeId,
      t.direction,
      fmtPrice(t.entry),
      fmtPrice(t.exit),
      String(t.durationMin),
      pnlPctCell,
      formatUsd(t.netUsd),
      ...(executionOnly ? [] : [formatUsd(t.grossUsd), formatUsd(t.tradeFeesUsd), formatUsd(t.fundingUsd), t.aiBand]),
    ];
  });

  if (executionOnly) {
    return (
      <div className="space-y-4">
        {positionRows.length === 0 && pendingRows.length === 0 ? (
          <div className="rounded-2xl border border-zinc-800/90 bg-zinc-950/50 px-4 py-5">
            <p className="text-xs font-semibold text-zinc-300">No active Bitfinex trade</p>
            <p className="mt-1 text-[11px] text-zinc-500">
              There is no open position or resting entry order on your exchange.
            </p>
          </div>
        ) : null}
        {positionRows.length > 0 ? (
          <MiniTable
            title="Open Bitfinex position"
            subtitle="One exchange position — virtual relay lots are not repeated here"
            headers={['Position', 'Side', 'Qty', 'Entry', 'Current', 'SL', 'TP', 'P/L']}
            rows={positionRows}
            emptyMessage="No open position."
          />
        ) : null}
        {pendingRows.length > 0 ? (
          <MiniTable
            title="Resting Bitfinex order"
            subtitle="One row per real exchange order"
            headers={['Trade ID', 'Side', 'State', 'Qty', 'Limit price', 'Age min']}
            rows={pendingRows}
            emptyMessage="No resting order."
          />
        ) : null}
        <MiniTable
          title="Completed trades & P/L"
          subtitle="Realized Bitfinex results — one row per completed trade"
          headers={[
            'Time (Melbourne)',
            'Trade ID',
            'Direction',
            'Entry',
            'Exit',
            'Duration min',
            'PnL %',
            'Net USD',
          ]}
          rows={tradeRows}
          emptyMessage="No closed trades this session."
        />
      </div>
    );
  }

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
        subtitle={positionSubtitle}
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
        subtitle="Created and expired/cancelled timestamps are both Melbourne time"
        headers={[
          'Created (Melbourne)',
          'Expired (Melbourne)',
          'Dir',
          'Limit price',
          'Age min',
          'Reason',
          'Conf',
          'Mode',
        ]}
        rows={expiredRows}
        emptyMessage="No recently expired orders."
      />
      <MiniTable
        title="Trades"
        subtitle="Closed session trades — Melbourne 24h · full trade ID matches relay fidelity"
        headers={[
          'Time (Melbourne)',
          'Trade ID',
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

'use client';

import { displayMelbourneTime, formatUsd, parseMelbourneTimestampMs, type TradingAgentDashboardState } from '@dcf/utils';
import { selectLiveExecutionBook } from '@/components/agent-hub/agent-live-execution-view';
import {
  chaseSelectionLabel,
  directionGap,
} from '@/components/agent-hub/agent-direction-gap';

/**
 * Age (minutes) between an expired order's creation and its terminal expiry.
 *
 * The bot populates `age_min` as `expired_ts - created_ts` at the moment of
 * expiry, which is correct. However, some legacy snapshots and alternate
 * mappers round it or omit it, and downstream viewers can then show "0" beside
 * two real timestamps that disagree. To keep the rendered Age column
 * internally consistent with the rendered Created / Expired columns, we prefer
 * the difference between the parsed timestamps and only fall back to the
 * server-supplied `ageMin` when one or both timestamps cannot be parsed.
 *
 * `parseMelbourneTimestampMs` understands the bot's pre-formatted Melbourne
 * strings (`2026-07-31 16:00:00 AEST`); plain `Date.parse` does not.
 */
function expiredAgeMin(o: {
  createdTime?: string;
  expiredTime?: string;
  time: string;
  ageMin: number;
}): number {
  const created = parseMelbourneTimestampMs(o.createdTime ?? o.time);
  const expired = parseMelbourneTimestampMs(o.expiredTime ?? o.time);
  if (created != null && expired != null && expired >= created) {
    return Math.max(0, Math.round((expired - created) / 60_000));
  }
  return Math.max(0, Math.round(o.ageMin ?? 0));
}

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

/** Display the canonical Showcase id for internally adopted/relinked fills. */
export function canonicalTradeIdForDisplay(tradeId: string | null | undefined): string {
  if (!tradeId) return '\u2014';
  const parts = tradeId.split(':');
  if (parts[0] === 'relink' && parts[2]) return parts[2];
  if (parts[0] === 'adopt' && parts[1]) return parts[1];
  return tradeId;
}

/** Human-readable display of the canonical terminal code retained in exports. */
export function displayExitCause(reason: string | null | undefined): string {
  const raw = String(reason ?? '').trim();
  if (!raw) return 'Not recorded';
  const sourceConfirmedPrefix = 'SOURCE_CONFIRMED_';
  if (raw.toUpperCase().startsWith(sourceConfirmedPrefix)) {
    return `Source-confirmed: ${displayExitCause(raw.slice(sourceConfirmedPrefix.length))}`;
  }
  const labels: Record<string, string> = {
    PROFIT_LOCK_LADDER: 'Profit lock (trailing)',
    TAKE_PROFIT: 'Take profit',
    THESIS_FAST_CUT: 'Thesis fast cut',
    EARLY_FAIL: 'Early thesis failure',
    STOP_LOSS: 'Stop loss',
    HARD_STOP: 'Safety stop',
    TIME_EXIT: 'Maximum-hold exit',
    ADMIN_MANUAL_CLOSE: 'Manual close',
    USER_RELAY_STOP: 'Relay stopped by user',
    CIRCUIT_BREAKER_ADMIN_MANUAL: 'Safety flat',
    SHOWCASE_MIRROR: 'Showcase exit mirrored',
    SOURCE_ABSENCE_FALLBACK: 'Source snapshot absence fallback',
    SHOWCASE_CLOSED: 'Showcase closed',
    EXCHANGE_STOP: 'Exchange protective stop',
    EXCHANGE_ALREADY_FLAT: 'Already flat on exchange',
    COPY_SCENARIO_C_PROFIT_LOCK: 'Copy profit lock (before Showcase fill)',
    COPY_SCENARIO_C_THESIS_FAST_CUT: 'Copy thesis cut (before Showcase fill)',
    COPY_SCENARIO_C_HARD_STOP: 'Copy safety stop (before Showcase fill)',
  };
  return labels[raw.toUpperCase()] ?? raw.replace(/_/g, ' ');
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
  /** Public execution-only view — one real exchange position / resting order /
   *  completed trades plus the session-scoped active relay signals and this-session
   *  expired orders. Omits the showcase source-bot's AI columns (raw gap, chase buckets,
   *  gross/fees/funding). */
  executionOnly?: boolean;
}) {
  const sourceBook = liveBook ?? EMPTY_LIVE_BOOK;
  const book = executionOnly ? selectLiveExecutionBook(sourceBook) : sourceBook;
  const cap = Math.max(1, Math.min(maxRows, 20));

  const signalRows = book.activeSignals.slice(0, cap).map((s) => {
    const gap = directionGap(s.rawScoreGap);
    return [
      displayMelbourneTime(s.time),
      s.tradeId ?? '—',
      s.direction,
      gap ? `${gap.raw}/100` : 'Not recorded',
      gap?.bucketLabel ?? '—',
      s.regime,
      s.strategy,
      s.trigger,
      s.chaseCount != null ? String(s.chaseCount) : '—',
      chaseSelectionLabel(s.selectedChaseBuckets),
      s.entryLimitPrice != null && s.entryLimitPrice > 0 ? fmtPrice(s.entryLimitPrice) : 'Not created',
      s.waitingReason ?? s.outcome,
      s.fillPrice != null ? fmtPrice(s.fillPrice) : '—',
      s.exitReason ?? '—',
    ];
  });

  const positionRows = book.positions.slice(0, cap).map((p) => [
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
          canonicalTradeIdForDisplay(o.tradeId) === '\u2014'
            ? 'Exchange order'
            : canonicalTradeIdForDisplay(o.tradeId),
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
    String(expiredAgeMin(o)),
    o.reason,
    o.mode,
  ]);

  // Live-copy active signals and expired orders come from the SUBSCRIBER session
  // book, which is scoped to this user's relay copy session. selectLiveExecutionBook
  // strips them (the public execution-only view historically only showed the
  // real exchange position / resting order / closed trades), but the subscriber
  // backend (mapSubscriberExchangeLiveBook) does populate them — pending relay
  // intents and this-session expired cycles are meaningful at session scope, so
  // we surface them here directly from the source book. The slim header set
  // omits the showcase-only AI columns (raw gap / chase buckets) which do not
  // exist for a copy subscriber.
  const liveSignalRows = sourceBook.activeSignals.slice(0, cap).map((s) => [
    displayMelbourneTime(s.time),
    s.tradeId ?? '—',
    s.direction,
    s.regime ?? '—',
    s.strategy ?? 'COPY',
    s.trigger ?? 'RELAY',
    s.signalPrice != null && s.signalPrice > 0 ? fmtPrice(s.signalPrice) : '—',
    s.waitingReason ?? s.outcome,
    s.fillPrice != null ? fmtPrice(s.fillPrice) : '—',
    s.exitReason ?? '—',
  ]);

  // Live-copy "expiredOrders" actually conflates two very different outcomes:
  //   1. A real exchange limit existed and then expired/cancelled (TTL, duplicate,
  //      price-moved-away). limitPrice > 0 and reason describes the order's end.
  //   2. No order was ever created — the relay intent was blocked pre-flight
  //      (SPREAD_BUCKET_BLOCKED, NO_EDGE, AI_REJECT, ...). limitPrice is 0/null
  //      and the row's reason is a *signal block reason*, not an order event.
  // Showing both in a single "Expired / blocked" table mislabels blocked signals
  // as if a real order had expired. Split them so each carries an honest label.
  const BLOCK_REASONS = new Set([
    'SPREAD_BUCKET_BLOCKED',
    'AI_REJECT',
    'AI_REJECTED',
    'NO_EDGE',
    'EDGE_BELOW_THRESHOLD',
    'PRE_ENTRY_BLOCKED',
    'BLOCKED',
    'POLICY_BLOCKED',
  ]);
  function isBlockedSignal(o: { limitPrice: number; reason: string }): boolean {
    if (!o) return false;
    const noOrderCreated = !o.limitPrice || o.limitPrice <= 0;
    const reasonUpper = String(o.reason ?? '').toUpperCase();
    const reasonIsBlock =
      BLOCK_REASONS.has(reasonUpper) || reasonUpper.endsWith('_BLOCKED');
    // Either signal is sufficient on its own — both together is the clearest case.
    return noOrderCreated || reasonIsBlock;
  }

  const liveExpired = sourceBook.expiredOrders.slice(0, cap);
  const liveGenuineExpiredRows = liveExpired
    .filter((o) => !isBlockedSignal(o))
    .map((o) => [
      displayMelbourneTime(o.createdTime ?? o.time),
      displayMelbourneTime(o.expiredTime ?? o.time),
      o.direction,
      fmtPrice(o.limitPrice),
      String(expiredAgeMin(o)),
      o.reason,
    ]);
  const liveBlockedSignalRows = liveExpired
    .filter((o) => isBlockedSignal(o))
    .map((o) => [
      displayMelbourneTime(o.createdTime ?? o.time),
      displayMelbourneTime(o.expiredTime ?? o.time),
      o.direction,
      // No real order existed — render the signal price if known, else 'No order'.
      o.limitPrice && o.limitPrice > 0 ? fmtPrice(o.limitPrice) : 'No order',
      String(expiredAgeMin(o)),
      o.reason || 'Blocked',
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
      canonicalTradeIdForDisplay(t.tradeId),
      t.direction,
      fmtPrice(t.entry),
      fmtPrice(t.exit),
      String(t.durationMin),
      displayExitCause(t.exitReason),
      pnlPctCell,
      formatUsd(t.netUsd),
      ...(executionOnly ? [] : [formatUsd(t.grossUsd), formatUsd(t.tradeFeesUsd), formatUsd(t.fundingUsd)]),
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
          title="Active relay signals"
          subtitle="Relay intents the copy engine is actioning on your exchange this session — scoped to your session only"
          headers={[
            'Time (Melbourne)',
            'Trade ID',
            'Dir',
            'Regime',
            'Strategy',
            'Trigger',
            'Signal price',
            'Status',
            'Fill price',
            'Exit reason',
          ]}
          rows={liveSignalRows}
          emptyMessage="No active relay signal on your session right now."
        />
        <MiniTable
          title="Expired relay orders"
          subtitle="Real exchange limit orders that ended without a fill — TTL expired, cancelled as duplicate, or price moved away. One row per real order that was actually created."
          headers={[
            'Created (Melbourne)',
            'Expired (Melbourne)',
            'Dir',
            'Limit price',
            'Age min',
            'Reason',
          ]}
          rows={liveGenuineExpiredRows}
          emptyMessage="No expired exchange orders on your session."
        />
        <MiniTable
          title="Blocked signals (no order created)"
          subtitle="Relay intents that were blocked before any exchange limit was placed. The reason is the signal's pre-entry block reason — no Bitfinex order ever existed for these rows."
          headers={[
            'Created (Melbourne)',
            'Blocked (Melbourne)',
            'Dir',
            'Limit price',
            'Age min',
            'Block reason',
          ]}
          rows={liveBlockedSignalRows}
          emptyMessage="No blocked relay signals on your session."
        />
        <MiniTable
          title="Completed trades & P/L"
          subtitle="Realized Bitfinex results — canonical Trade ID matches Showcase; only exchange-filled trades appear here"
          headers={[
            'Time (Melbourne)',
            'Trade ID',
            'Direction',
            'Entry',
            'Exit',
            'Duration min',
            'Exit cause',
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
        subtitle="Approvals and virtual-chase candidates. They are not real pending orders until an exact limit appears below."
        headers={[
          'Time',
          'Trade ID',
          'Dir (final)',
          'Raw AI gap',
          'Gap bucket',
          'Regime',
          'Strategy',
          'Stage',
          'Chase now',
          'Entry buckets',
          'Exact limit',
          'Status / waiting reason',
          'Fill price',
          'Exit reason',
        ]}
        rows={signalRows}
        emptyMessage="No approval or virtual-chase candidate right now."
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
        subtitle="Executable resting limit orders only — virtual chase candidates remain in Active signals"
        headers={['Age min', 'Side', 'Status', 'Qty', 'Limit price', 'Signal price']}
        rows={pendingRows}
        emptyMessage="No real resting limit order. An approved candidate may still be waiting virtually above."
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
          'Exit cause',
          'PnL %',
          'Net USD',
          'Gross USD',
          'Trade fees',
          'Funding',
        ]}
        rows={tradeRows}
        emptyMessage="No completed trades in this session yet."
      />
    </div>
  );
}

'use client';

import { useMemo } from 'react';
import { formatPercent, formatUsd, type TradingAgentDashboardState } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

function fmtPrice(n: number): string {
  if (!n || !Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function pctFromEntry(entry: number, level: number, side: string): number {
  if (!entry || !level) return 0;
  const raw = side === 'SHORT' ? ((entry - level) / entry) * 100 : ((level - entry) / entry) * 100;
  return raw;
}

type LevelLine = {
  key: string;
  label: string;
  price: number;
  tone: 'entry' | 'stop' | 'tp1' | 'tp2' | 'current' | 'pending';
  dashed?: boolean;
};

function buildLevels(input: {
  currentPrice: number;
  position?: TradingAgentDashboardState['liveBook']['positions'][0];
  pendingLimit?: number;
}): LevelLine[] {
  const { currentPrice, position, pendingLimit } = input;
  const lines: LevelLine[] = [];

  if (position && position.entry > 0) {
    const side = position.side;
    const entry = position.entry;
    const stop = position.stopLoss;
    const tp = position.takeProfit;
    const tp1 =
      tp > 0 && entry > 0 ? entry + (side === 'SHORT' ? -1 : 1) * Math.abs(tp - entry) * 0.5 : 0;

    if (tp > 0) lines.push({ key: 'tp2', label: 'TP2', price: tp, tone: 'tp2' });
    if (tp1 > 0 && tp1 !== tp) lines.push({ key: 'tp1', label: 'TP1', price: tp1, tone: 'tp1', dashed: true });
    lines.push({ key: 'entry', label: 'ENTRY', price: entry, tone: 'entry' });
    if (stop > 0) lines.push({ key: 'stop', label: 'STOP', price: stop, tone: 'stop' });
  } else if (pendingLimit && pendingLimit > 0) {
    lines.push({ key: 'pending', label: 'LIMIT', price: pendingLimit, tone: 'pending', dashed: true });
  }

  if (currentPrice > 0) {
    lines.push({ key: 'current', label: 'CURRENT', price: currentPrice, tone: 'current' });
  }

  return lines.sort((a, b) => b.price - a.price);
}

function TradeMapChart({ lines }: { lines: LevelLine[] }) {
  const prices = lines.map((l) => l.price).filter((p) => p > 0);
  if (prices.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-xl border border-zinc-800/80 bg-black/30 text-sm text-zinc-500">
        No active trade — waiting for the next execution.
      </div>
    );
  }

  const min = Math.min(...prices) * 0.9995;
  const max = Math.max(...prices) * 1.0005;
  const span = max - min || 1;

  const yFor = (price: number) => `${((max - price) / span) * 100}%`;

  const toneClass: Record<LevelLine['tone'], string> = {
    entry: 'border-amber-400/90 text-amber-200',
    stop: 'border-red-500/70 text-red-300',
    tp1: 'border-emerald-500/50 text-emerald-300/80',
    tp2: 'border-emerald-400 text-emerald-200',
    current: 'border-violet-400 text-violet-200',
    pending: 'border-zinc-500 text-zinc-400',
  };

  return (
    <div className="relative h-72 overflow-hidden rounded-xl border border-zinc-800/80 bg-gradient-to-b from-zinc-950 to-black/60 px-4 py-3">
      <div className="absolute inset-0 opacity-20">
        {[0, 25, 50, 75, 100].map((pct) => (
          <div
            key={pct}
            className="absolute left-0 right-0 border-t border-zinc-700/40"
            style={{ top: `${pct}%` }}
          />
        ))}
      </div>
      <div className="relative h-full">
        {lines.map((line) => (
          <div
            key={line.key}
            className="absolute left-0 right-0 flex items-center gap-2"
            style={{ top: yFor(line.price), transform: 'translateY(-50%)' }}
          >
            <span className={`w-14 shrink-0 text-right text-[10px] font-bold uppercase tracking-wider ${toneClass[line.tone]}`}>
              {line.label}
            </span>
            <div
              className={`h-0 flex-1 border-t-2 ${toneClass[line.tone]} ${line.dashed ? 'border-dashed opacity-70' : ''}`}
            />
            <span className="w-20 shrink-0 text-right font-mono text-xs text-zinc-200">
              {fmtPrice(line.price)}
            </span>
            {line.tone === 'current' && (
              <span className="inline-flex h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-violet-400 shadow-[0_0_10px_rgba(167,139,250,0.9)]" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function AgentExecutionChart({
  dashboard,
  agent,
  liveBook,
}: {
  dashboard: TradingAgentDashboardState;
  agent: TradingAgentSummary;
  liveBook?: TradingAgentDashboardState['liveBook'];
}) {
  const book = liveBook ?? dashboard.liveBook;
  const position = book.positions[0];
  const pending = book.pendingOrders[0];
  const currentPrice = dashboard.currentPrice || position?.current || 0;

  const lines = useMemo(
    () =>
      buildLevels({
        currentPrice,
        position,
        pendingLimit: pending?.limitPrice,
      }),
    [currentPrice, position, pending?.limitPrice],
  );

  const side = position?.side ?? pending?.side ?? '—';
  const entry = position?.entry ?? pending?.limitPrice ?? 0;
  const stop = position?.stopLoss ?? 0;
  const tp = position?.takeProfit ?? 0;

  const riskPct = entry && stop ? Math.abs(pctFromEntry(entry, stop, side)) : 0;
  const rewardPct = entry && tp ? Math.abs(pctFromEntry(entry, tp, side)) : 0;
  const rr = riskPct > 0 ? rewardPct / riskPct : 0;

  const unrealPct =
    position && entry
      ? pctFromEntry(entry, currentPrice, position.side)
      : 0;

  const recentTrades = book.trades.slice(0, 8);

  return (
    <section className="rounded-2xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/15 to-zinc-950/60 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-300">
            Live trade map
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Watch execution — not strategy</h2>
          <p className="mt-1 max-w-xl text-xs text-zinc-500">
            Public view: entry, risk, targets, and P&amp;L only. Signal logic stays private.
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">BTC / USD</p>
          <p className="font-mono text-2xl font-bold text-white">{fmtPrice(currentPrice)}</p>
        </div>
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <TradeMapChart lines={lines} />

        <div className="space-y-3">
          {position ? (
            <div className="rounded-xl border border-emerald-500/30 bg-black/30 p-4">
              <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-300">
                {position.side} OPEN
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                <div>
                  <dt className="text-zinc-500">Entry</dt>
                  <dd className="font-mono font-semibold text-white">{fmtPrice(position.entry)}</dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Current</dt>
                  <dd className="font-mono font-semibold text-violet-200">{fmtPrice(currentPrice)}</dd>
                </div>
                <div>
                  <dt className="text-zinc-500">PnL</dt>
                  <dd className={`font-semibold ${unrealPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatPercent(unrealPct)} · {formatUsd(position.pnlUsd, 2)}
                  </dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Qty</dt>
                  <dd className="font-mono text-zinc-200">{position.qty.toFixed(4)} BTC</dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Stop</dt>
                  <dd className="font-mono text-red-300">{fmtPrice(position.stopLoss)}</dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Target</dt>
                  <dd className="font-mono text-emerald-300">{fmtPrice(position.takeProfit)}</dd>
                </div>
              </dl>
            </div>
          ) : pending ? (
            <div className="rounded-xl border border-zinc-700 bg-black/30 p-4 text-sm text-zinc-300">
              <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Order pending</p>
              <p className="mt-2">
                {pending.side} limit @ <span className="font-mono text-white">{fmtPrice(pending.limitPrice)}</span>
              </p>
            </div>
          ) : (
            <div className="rounded-xl border border-zinc-800 bg-black/20 p-4 text-sm text-zinc-500">
              Flat — no open position or pending order.
            </div>
          )}

          <div className="grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-lg border border-red-500/30 bg-red-950/20 px-2 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">Risk</p>
              <p className="mt-0.5 font-bold text-red-300">-{riskPct.toFixed(2)}%</p>
            </div>
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-2 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">Reward</p>
              <p className="mt-0.5 font-bold text-emerald-300">+{rewardPct.toFixed(2)}%</p>
            </div>
            <div className="rounded-lg border border-violet-500/30 bg-violet-950/20 px-2 py-2.5">
              <p className="text-[10px] uppercase text-zinc-500">R:R</p>
              <p className="mt-0.5 font-bold text-violet-200">{rr > 0 ? rr.toFixed(2) : '—'}</p>
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800/80 bg-black/20 px-3 py-2.5">
            <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Session</p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-300">
              <span>Balance {formatUsd(agent.balanceUsd ?? 0, 0)}</span>
              <span>P&amp;L {formatUsd(agent.sessionPnlUsd ?? 0, 2)}</span>
              <span>Win rate {(agent.winRatePct ?? 0).toFixed(1)}%</span>
              <span>{agent.tradeCount ?? 0} trades</span>
            </div>
          </div>
        </div>
      </div>

      {recentTrades.length > 0 && (
        <div className="mt-5 overflow-x-auto rounded-xl border border-zinc-800/80">
          <table className="w-full min-w-[520px] text-left text-[11px]">
            <thead>
              <tr className="border-b border-zinc-800 text-[10px] uppercase tracking-wide text-zinc-500">
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Side</th>
                <th className="px-3 py-2">Entry</th>
                <th className="px-3 py-2">Exit</th>
                <th className="px-3 py-2">PnL</th>
                <th className="px-3 py-2">Net</th>
              </tr>
            </thead>
            <tbody>
              {recentTrades.map((t) => (
                <tr key={t.tradeId} className="border-b border-zinc-900/80 text-zinc-300 last:border-0">
                  <td className="whitespace-nowrap px-3 py-2">{t.time}</td>
                  <td className="px-3 py-2">{t.direction}</td>
                  <td className="px-3 py-2 font-mono">{fmtPrice(t.entry)}</td>
                  <td className="px-3 py-2 font-mono">{fmtPrice(t.exit)}</td>
                  <td className={`px-3 py-2 font-semibold ${t.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {t.pnlPct >= 0 ? '+' : ''}
                    {t.pnlPct.toFixed(2)}%
                  </td>
                  <td className="px-3 py-2">{formatUsd(t.netUsd, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

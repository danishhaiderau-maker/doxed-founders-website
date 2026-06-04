'use client';

import { formatPercent } from '@dcf/utils';
import type { TraderVerifiedStatsPayload } from '@/lib/api';

type Props = {
  stats: TraderVerifiedStatsPayload;
  portfolioRoi: number;
};

export function TraderVerifiedStatsPanel({ stats, portfolioRoi }: Props) {
  if (stats.verifiedTrades === 0) {
    return (
      <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
        <h3 className="font-semibold">Verified track record</h3>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No closed paper trades yet. Wins and losses are scored from platform-recorded exits —
          not self-reported claims.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-emerald-500/20 bg-[var(--color-card)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">Verified track record</h3>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            Platform-signed paper exits · P&amp;L from fills, not manual win claims
          </p>
        </div>
        <div className="rounded-lg bg-emerald-950/40 px-3 py-2 text-center">
          <p className="text-[10px] font-medium uppercase tracking-wider text-emerald-400/80">
            Trader score
          </p>
          <p className="text-2xl font-bold text-emerald-300">{stats.traderScore}</p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Metric label="Verified trades" value={String(stats.verifiedTrades)} />
        <Metric label="Win rate" value={`${stats.winRatePct}%`} />
        <Metric
          label="Profit factor"
          value={stats.profitFactor != null ? stats.profitFactor.toFixed(2) : '—'}
        />
        <Metric
          label="Avg R:R"
          value={stats.averageRR != null ? stats.averageRR.toFixed(2) : '—'}
        />
        <Metric
          label="30D ROI"
          value={
            stats.roi30dPct != null ? formatPercent(stats.roi30dPct) : '—'
          }
          accent={
            stats.roi30dPct != null
              ? stats.roi30dPct >= 0
                ? 'green'
                : 'red'
              : undefined
          }
        />
        <Metric
          label="All-time ROI"
          value={formatPercent(portfolioRoi)}
          accent={portfolioRoi >= 0 ? 'green' : 'red'}
        />
        <Metric label="Max drawdown" value={`${stats.maxDrawdownPct}%`} />
        <Metric label="Net P&amp;L" value={`$${stats.netPnlUsd.toLocaleString()}`} />
        <Metric label="W / L" value={`${stats.wins} / ${stats.losses}`} />
        <Metric label="Consistency" value={`${stats.consistencyScore}`} />
      </div>

      <p className="mt-4 text-[10px] leading-relaxed text-zinc-500">
        Score weights: verified ROI 40%, profit factor 25%, consistency 15%, trade count 10%,
        win rate 10%. Exchange or wallet verification can be added later for live fills.
      </p>
    </section>
  );
}

function Metric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'green' | 'red';
}) {
  const color =
    accent === 'green'
      ? 'text-emerald-400'
      : accent === 'red'
        ? 'text-red-400'
        : 'text-white';

  return (
    <div className="rounded-lg bg-[var(--color-background)] px-3 py-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-muted)]">
        {label}
      </p>
      <p className={`mt-1 text-base font-semibold ${color}`}>{value}</p>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { TRADER_SCORE_FORMULA_NOTE } from '@dcf/utils';
import { TraderRankTabs } from '@/components/trader-rank-tabs';

export function PredictionTraderRankPanel() {
  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-5 text-sm">
        <h2 className="text-lg font-bold text-white">Trading rank (paper Alpha)</h2>
        <p className="mt-2 text-zinc-400">
          This leaderboard measures{' '}
          <strong className="text-amber-200">verified paper trading skill</strong> — not prediction
          accuracy. Wins and losses come from platform-recorded exits, not self-reported claims.
        </p>
        <p className="mt-3 rounded-lg bg-black/30 px-3 py-2 font-mono text-[11px] text-amber-200/90">
          Trader score (0–100): {TRADER_SCORE_FORMULA_NOTE}
        </p>
        <ul className="mt-4 list-inside list-disc space-y-1 text-xs text-zinc-500">
          <li>
            <strong className="text-zinc-400">Profit factor</strong> — gross wins ÷ gross losses on
            closed round-trips.
          </li>
          <li>
            <strong className="text-zinc-400">Average R:R</strong> — realized P&amp;L vs planned
            risk from entry + stop snapshot.
          </li>
          <li>
            <strong className="text-zinc-400">Max drawdown</strong> — peak-to-trough on cumulative
            paper P&amp;L curve.
          </li>
          <li>
            Win rate is only 10% of the score — a 90% win rate with huge losses ranks below a 50%
            win rate with strong profit factor.
          </li>
        </ul>
        <Link
          href="/predict?tab=oracle"
          className="mt-4 inline-block text-xs text-indigo-400 hover:underline"
        >
          Forecasting uses Oracle rank instead →
        </Link>
      </section>

      <TraderRankTabs compact />
    </div>
  );
}

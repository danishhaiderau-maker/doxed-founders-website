'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { formatDdollar, ORACLE_SCORE_FORMULA_NOTE, predictionDifficultyWeight } from '@dcf/utils';
import { fetchOracleLeaderboard, OracleLeaderboardEntry } from '@/lib/api';

function ExampleDifficulty() {
  const easy = predictionDifficultyWeight(0.99);
  const hard = predictionDifficultyWeight(0.2);
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <div className="rounded-lg border border-zinc-800 bg-black/40 p-3 text-xs">
        <p className="font-semibold text-zinc-400">Easy call (99% implied)</p>
        <p className="mt-1 text-zinc-500">“BTC above $50k?” — correct win</p>
        <p className="mt-2 font-mono text-amber-300">Difficulty weight: {easy}</p>
        <p className="mt-1 text-[10px] text-zinc-600">Almost no Oracle points</p>
      </div>
      <div className="rounded-lg border border-emerald-500/25 bg-emerald-950/15 p-3 text-xs">
        <p className="font-semibold text-emerald-300">Hard call (20% implied)</p>
        <p className="mt-1 text-zinc-500">“Founder ships in 45 days” — correct win</p>
        <p className="mt-2 font-mono text-emerald-300">Difficulty weight: {hard}</p>
        <p className="mt-1 text-[10px] text-zinc-600">Large Oracle score gain</p>
      </div>
    </div>
  );
}

export function PredictionOracleRankPanel() {
  const [entries, setEntries] = useState<OracleLeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchOracleLeaderboard()
      .then((rows) => {
        setEntries(rows);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-indigo-500/30 bg-indigo-950/20 p-5 text-sm">
        <h2 className="text-lg font-bold text-white">Oracle rank (forecasting)</h2>
        <p className="mt-2 text-zinc-400">
          Separate from trading rank. Oracle score rewards{' '}
          <strong className="text-indigo-200">risk-adjusted accuracy</strong> on resolved
          prediction markets — not raw win rate.
        </p>
        <p className="mt-3 rounded-lg bg-black/30 px-3 py-2 font-mono text-[11px] text-indigo-200/90">
          {ORACLE_SCORE_FORMULA_NOTE}
        </p>
        <ExampleDifficulty />
        <ul className="mt-4 list-inside list-disc space-y-1 text-xs text-zinc-500">
          <li>Only resolved markets count — open stakes are excluded until settlement.</li>
          <li>
            Implied probability uses the pool split on your side when the market closed (parimutuel
            snapshot).
          </li>
          <li>
            Conviction multipliers (Low → Extreme) apply when we record conviction on stakes — full
            rollout on the stake flow is next.
          </li>
          <li>
            This is not gambling ROI — it is public forecasting reputation inside DDollar.
          </li>
        </ul>
      </section>

      <section className="overflow-hidden rounded-xl border border-[var(--color-border)]">
        <table className="w-full text-left text-sm">
          <thead className="bg-[var(--color-card)] text-[var(--color-muted)]">
            <tr>
              <th className="px-4 py-3 font-medium">#</th>
              <th className="px-4 py-3 font-medium">Forecaster</th>
              <th className="px-4 py-3 font-medium">Oracle score</th>
              <th className="px-4 py-3 font-medium">Accuracy</th>
              <th className="px-4 py-3 font-medium">W / L</th>
              <th className="px-4 py-3 font-medium">Avg difficulty</th>
              <th className="px-4 py-3 font-medium">Net DD</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-muted)]">
                  Loading oracle rank…
                </td>
              </tr>
            )}
            {error && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-red-300">
                  {error}
                </td>
              </tr>
            )}
            {!loading && !error && entries.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-muted)]">
                  No resolved markets yet — stake on live markets, then check back after settlement.
                </td>
              </tr>
            )}
            {entries.map((entry, index) => (
              <tr key={entry.userId} className="border-t border-[var(--color-border)]">
                <td className="px-4 py-3 font-semibold">#{index + 1}</td>
                <td className="px-4 py-3">
                  <Link
                    href={`/portfolio/${entry.userId}`}
                    className="font-medium hover:text-indigo-300"
                  >
                    {entry.displayName}
                  </Link>
                </td>
                <td className="px-4 py-3 font-semibold text-indigo-300">{entry.oracleScore}</td>
                <td className="px-4 py-3">{entry.accuracyPct}%</td>
                <td className="px-4 py-3 text-zinc-400">
                  {entry.marketsWon} / {entry.marketsLost}
                </td>
                <td className="px-4 py-3">{entry.avgDifficulty}</td>
                <td
                  className={`px-4 py-3 ${
                    entry.netDdollarUsd >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  {formatDdollar(entry.netDdollarUsd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="text-xs text-zinc-500">
        Trader rankings use verified paper exits —{' '}
        <Link href="/predict?tab=traders" className="text-amber-400 hover:underline">
          see Trading rank tab
        </Link>
        .
      </p>
    </div>
  );
}

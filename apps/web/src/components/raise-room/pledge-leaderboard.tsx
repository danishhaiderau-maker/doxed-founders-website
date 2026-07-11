'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchTokenLaunchPledges,
  type TokenLaunchPledgeLeaderboard,
} from '@/lib/api';

/**
 * PledgeLeaderboard — top pledgers for a project, with their pledge amount
 * and projected token allocation. The "scouts" surface — who believed early.
 */
export function PledgeLeaderboard({ projectId }: { projectId: string }) {
  const [data, setData] = useState<TokenLaunchPledgeLeaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetchTokenLaunchPledges(projectId, 25);
      setData(d);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load pledgers');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Pledge Leaderboard
        </h3>
        <span className="text-[11px] text-zinc-600">
          Pool: {data?.totalPoolTokens.toLocaleString() ?? '—'} tokens (
          {data?.pledgePoolPercent ?? 5}%)
        </span>
      </div>

      {loading && (
        <p className="mt-4 text-sm text-zinc-500">Loading pledgers…</p>
      )}

      {error && (
        <p className="mt-4 text-sm text-red-300">{error}</p>
      )}

      {!loading && !error && data && data.leaderboard.length === 0 && (
        <p className="mt-4 text-sm text-zinc-500">
          No pledgers yet. Be the first to commit DDollar.
        </p>
      )}

      {!loading && !error && data && data.leaderboard.length > 0 && (
        <ol className="mt-4 space-y-2">
          {data.leaderboard.map((row) => (
            <li
              key={row.userId}
              className="flex items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2"
            >
              <div className="flex items-center gap-3">
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                    row.rank === 1
                      ? 'bg-amber-500/20 text-amber-300'
                      : row.rank === 2
                        ? 'bg-zinc-500/20 text-zinc-300'
                        : row.rank === 3
                          ? 'bg-orange-700/20 text-orange-300'
                          : 'bg-zinc-800 text-zinc-500'
                  }`}
                >
                  {row.rank}
                </span>
                <div>
                  <div className="text-sm text-zinc-200">
                    {row.userName ?? row.userHandle ?? 'Scout'}
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    {row.sharePct.toFixed(1)}% of pool
                  </div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-sm font-semibold text-zinc-100">
                  {row.amount.toLocaleString()} DD
                </div>
                <div className="text-[11px] text-emerald-400">
                  ~{Math.round(row.projectedTokens).toLocaleString()} tokens
                  {row.allocatedTokens !== null && (
                    <span className="text-zinc-500">
                      {' '}
                      · allocated
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

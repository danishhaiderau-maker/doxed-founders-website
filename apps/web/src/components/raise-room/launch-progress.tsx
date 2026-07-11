'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchTokenLaunchStatus,
  type TokenLaunchStatusResponse,
} from '@/lib/api';

/**
 * LaunchProgress — the 15-day commitment window progress. Shows days
 * remaining, total committed, live mint address (with Solana explorer link
 * once minted), and the post-launch checklist surface.
 *
 * Polls every 30s while the window is open so the days-remaining counter
 * stays fresh without a manual refresh.
 */
export function LaunchProgress({ launchId }: { launchId: string }) {
  const [status, setStatus] = useState<TokenLaunchStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchTokenLaunchStatus(launchId);
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load launch');
    } finally {
      setLoading(false);
    }
  }, [launchId]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 30000);
    return () => clearInterval(t);
  }, [load]);

  if (loading && !status) {
    return (
      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-5 text-sm text-zinc-500">
        Loading launch…
      </div>
    );
  }

  if (error || !status) {
    return (
      <div className="rounded-2xl border border-red-500/30 bg-red-950/20 p-5 text-sm text-red-200">
        {error ?? 'Launch unavailable'}
      </div>
    );
  }

  const windowPct =
    status.windowClosesAt && status.launchDate
      ? Math.min(
          100,
          Math.max(
            0,
            Math.round(
              ((Date.now() - new Date(status.launchDate).getTime()) /
                (new Date(status.windowClosesAt).getTime() -
                  new Date(status.launchDate).getTime())) *
                100,
            ),
          ),
        )
      : 0;

  const isWindowOpen = status.status === 'WINDOW_OPEN';
  const isLive = status.status === 'LIVE';

  return (
    <div className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/30 via-zinc-950/60 to-fuchsia-950/20 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">
              {status.status === 'PLEDGING'
                ? 'Pre-launch'
                : isWindowOpen
                  ? 'Commitment window open'
                  : isLive
                    ? 'Live — trading open'
                    : 'Closed'}
            </span>
            {isLive && (
              <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold uppercase text-emerald-300">
                Live
              </span>
            )}
          </div>
          <h3 className="mt-1 text-lg font-semibold text-zinc-100">
            {status.project.name}{' '}
            <span className="text-zinc-500">${status.project.ticker}</span>
          </h3>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-violet-300">
            {status.pledged.toLocaleString()}
          </div>
          <div className="text-[11px] text-zinc-500">DDollar committed</div>
        </div>
      </div>

      {(isWindowOpen || isLive) && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>Commitment window</span>
            <span>
              {isLive
                ? 'Finalized'
                : status.daysRemaining !== null
                  ? `${status.daysRemaining} day${status.daysRemaining === 1 ? '' : 's'} left`
                  : '—'}
            </span>
          </div>
          <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-900">
            <div
              className="h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500"
              style={{ width: `${windowPct}%` }}
            />
          </div>
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric
          label="Pledgers"
          value={status.totalPledgers.toLocaleString()}
        />
        <Metric
          label="Pool %"
          value={`${status.pledgePoolPercent}%`}
        />
        <Metric
          label="Supply"
          value={status.supply.toLocaleString()}
        />
        <Metric
          label="Initial price"
          value={`$${status.initialPrice.toFixed(6)}`}
        />
      </div>

      {status.solanaMint && (
        <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-wider text-zinc-500">
                Solana devnet mint
              </div>
              <code className="block truncate text-xs text-emerald-300">
                {status.solanaMint}
              </code>
            </div>
            {status.solanaExplorerUrl && (
              <a
                href={status.solanaExplorerUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 rounded-md border border-violet-500/40 bg-violet-950/30 px-2.5 py-1 text-[11px] font-semibold text-violet-200 hover:bg-violet-900/40"
              >
                Explorer ↗
              </a>
            )}
          </div>
        </div>
      )}

      {isLive && (
        <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-950/15 p-3 text-xs text-amber-200">
          <strong>Post-launch checklist:</strong> post your DEXScreener link
          and confirm your Twitter handle within 48h to keep your Doxxed
          Builder status.
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className="text-sm font-semibold text-zinc-100">{value}</div>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import {
  DISCOVER_UNIVERSE_COLORS,
  type DiscoverTimeframe,
  type DiscoverUniverseStage,
} from '@dcf/utils';
import {
  fetchMyDiscoverVisibility,
  type DiscoverMyVisibilityResponse,
} from '@/lib/api';

const TIMEFRAMES: DiscoverTimeframe[] = ['1h', '6h', '24h', '7d'];

export function DiscoverMyVisibilityPanel() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [timeframe, setTimeframe] = useState<DiscoverTimeframe>('24h');
  const [data, setData] = useState<DiscoverMyVisibilityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchMyDiscoverVisibility(token, timeframe)
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [token, timeframe]);

  if (!token) return null;

  const stage = data?.project.universeStage as DiscoverUniverseStage | undefined;
  const colors = stage ? DISCOVER_UNIVERSE_COLORS[stage] : null;

  return (
    <section className="rounded-xl border border-blue-500/25 bg-blue-950/15 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-blue-400">
            Discover visibility
          </p>
          <h3 className="mt-1 text-base font-bold text-white">Your bubble on /discover</h3>
        </div>
        <div className="flex rounded-lg border border-zinc-800 p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              className={`rounded-md px-2 py-0.5 text-[10px] uppercase ${
                timeframe === tf ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-red-300">{error}</p>}

      {!data && !error && (
        <p className="mt-3 text-sm text-zinc-500">Loading visibility breakdown…</p>
      )}

      {data && colors && (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span
              className="h-4 w-4 rounded-full border-[3px]"
              style={{ borderColor: colors.border }}
              aria-hidden
            />
            <span className="text-sm text-zinc-300">
              <strong className="text-white">{data.project.name}</strong> · ring:{' '}
              <span style={{ color: colors.border }}>{colors.label}</span>
              {data.project.recentlyListed && (
                <span className="ml-2 text-violet-300">(new listing — still {colors.label})</span>
              )}
            </span>
            <span className="text-sm tabular-nums text-zinc-400">
              Activity {data.project.activityScore}/100 · Conviction {data.project.convictionScore}
            </span>
          </div>

          <ul className="mt-4 space-y-2">
            {data.breakdown.factors.map((f) => {
              const pct = f.maxPoints > 0 ? Math.round((f.points / f.maxPoints) * 100) : 0;
              return (
                <li key={f.key}>
                  <div className="flex justify-between text-xs text-zinc-400">
                    <span>{f.label}</span>
                    <span>
                      {Math.round(f.points)}/{f.maxPoints} pts
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full rounded-full bg-blue-500/80 transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>

          {data.tips.length > 0 && (
            <div className="mt-4 rounded-lg border border-zinc-800/80 bg-black/30 px-3 py-2">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                Quick wins
              </p>
              <ul className="mt-1 space-y-1 text-xs text-zinc-400">
                {data.tips.map((t) => (
                  <li key={t}>→ {t}</li>
                ))}
              </ul>
            </div>
          )}

          <Link
            href="/discover"
            className="mt-4 inline-block text-xs font-semibold text-blue-300 hover:text-blue-200"
          >
            View on Discover map →
          </Link>
        </>
      )}
    </section>
  );
}

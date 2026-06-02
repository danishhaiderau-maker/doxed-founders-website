'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import type { DiscoverUniverseResponse } from '@/lib/api';

export function FeedDdFlowBar({ sidebar }: { sidebar: DiscoverUniverseResponse['sidebar'] }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/60 px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        DDollar Flow · 24h
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="text-[10px] uppercase text-emerald-500/80">Top Inflow</span>
        {sidebar.topInflow.length === 0 && (
          <span className="text-zinc-600">No inflow yet</span>
        )}
        {sidebar.topInflow.slice(0, 3).map((p, i) => (
          <Link
            key={p.slug}
            href={`/project/${p.slug}`}
            className="font-medium hover:text-emerald-300"
          >
            <span className="text-zinc-300">{p.ticker}</span>{' '}
            <span className="text-emerald-400">+{formatUsd(p.ddInflow24h, 0)}</span>
            {i < 2 && sidebar.topInflow[i + 1] && (
              <span className="ml-4 hidden text-zinc-700 sm:inline">|</span>
            )}
          </Link>
        ))}
        {sidebar.topOutflow[0] && (
          <>
            <span className="text-[10px] uppercase text-red-500/80">Top Outflow</span>
            <Link
              href={`/project/${sidebar.topOutflow[0].slug}`}
              className="font-medium hover:text-red-300"
            >
              <span className="text-zinc-300">{sidebar.topOutflow[0].ticker}</span>{' '}
              <span className="text-red-400">
                -{formatUsd(sidebar.topOutflow[0].ddOutflow ?? 0, 0)}
              </span>
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

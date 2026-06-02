'use client';

import { useMemo, useState } from 'react';
import {
  DISCOVER_UNIVERSE_COLORS,
  layoutBubblePositions,
  type DiscoverTimeframe,
  type DiscoverUniverseStage,
} from '@dcf/utils';
import type { DiscoverUniverseProject, DiscoverUniverseStageFilter } from '@/lib/api';
import { DiscoverUniverseBubble } from './discover-universe-bubble';

const STAGE_FILTERS: { key: DiscoverUniverseStageFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'building', label: 'Building' },
  { key: 'validation', label: 'Validation' },
  { key: 'live', label: 'Live' },
  { key: 'recently_listed', label: 'Recently Listed' },
];

const TIMEFRAMES: DiscoverTimeframe[] = ['1h', '6h', '24h', '7d'];

export function DiscoverUniverseMap({
  projects,
  chains,
  stageFilter,
  chainSlug,
  timeframe,
  onStageFilter,
  onChainSlug,
  onTimeframe,
}: {
  projects: DiscoverUniverseProject[];
  chains: { slug: string; name: string }[];
  stageFilter: DiscoverUniverseStageFilter;
  chainSlug: string;
  timeframe: DiscoverTimeframe;
  onStageFilter: (v: DiscoverUniverseStageFilter) => void;
  onChainSlug: (v: string) => void;
  onTimeframe: (v: DiscoverTimeframe) => void;
}) {
  const [dims] = useState({ w: 720, h: 480 });
  const positions = useMemo(
    () => layoutBubblePositions(Math.min(projects.length, 24), dims.w, dims.h),
    [projects.length, dims.w, dims.h],
  );

  return (
    <div className="relative overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#030308]">
      {/* Cosmic background */}
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            'radial-gradient(ellipse 80% 60% at 50% 40%, rgba(88,28,135,0.35), transparent 60%), radial-gradient(ellipse 60% 50% at 70% 70%, rgba(16,185,129,0.12), transparent 50%)',
        }}
      />
      <div className="pointer-events-none absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48Y2lyY2xlIGN4PSIxMCIgY3k9IjEwIiByPSIxIiBmaWxsPSJyZ2JhKDI1NSwyNTUsMjU1LDAuMykiLz48Y2lyY2xlIGN4PSI4MCIgY3k9IjQwIiByPSIwLjUiIGZpbGw9InJnYmEoMjU1LDI1NSwyNTUsMC4yKSIvPjwvc3ZnPg==')] opacity-30" />

      {/* Filters */}
      <div className="relative z-10 flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800/60 px-4 py-3">
        <div className="flex flex-wrap gap-1.5">
          {STAGE_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => onStageFilter(f.key)}
              className={`rounded-lg px-2.5 py-1 text-xs transition ${
                stageFilter === f.key
                  ? 'bg-white/10 font-semibold text-white'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={chainSlug}
            onChange={(e) => onChainSlug(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-900/80 px-2 py-1 text-xs text-zinc-300"
          >
            <option value="">All Chains</option>
            {chains.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
          </select>
          <div className="flex rounded-lg border border-zinc-800 p-0.5">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => onTimeframe(tf)}
                className={`rounded-md px-2 py-0.5 text-[10px] uppercase ${
                  timeframe === tf ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="relative z-10 flex flex-wrap gap-3 px-4 py-2 text-[10px] uppercase tracking-wider text-zinc-500">
        {(Object.keys(DISCOVER_UNIVERSE_COLORS) as DiscoverUniverseStage[]).map((key) => {
          const meta = DISCOVER_UNIVERSE_COLORS[key];
          return (
            <span key={key} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: meta.color }} />
              {meta.label}
            </span>
          );
        })}
      </div>

      {/* Bubble canvas */}
      <div className="relative mx-auto w-full max-w-[720px]" style={{ height: dims.h }}>
        {projects.length === 0 ? (
          <p className="flex h-full items-center justify-center text-sm text-zinc-500">
            No projects match these filters
          </p>
        ) : (
          projects.slice(0, 24).map((p, i) => (
            <DiscoverUniverseBubble
              key={p.slug}
              project={p}
              x={positions[i]?.x ?? dims.w / 2}
              y={positions[i]?.y ?? dims.h / 2}
              index={i}
            />
          ))
        )}
      </div>
    </div>
  );
}

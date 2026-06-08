'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DISCOVER_RECENTLY_LISTED_FILTER_LABEL,
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
  const canvasRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ w: 960, h: 560 });
  const [offsets, setOffsets] = useState<Record<string, { x: number; y: number }>>({});
  const [draggingSlug, setDraggingSlug] = useState<string | null>(null);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => {
      const w = Math.max(320, el.clientWidth);
      const h = Math.round(Math.min(600, Math.max(480, w * 0.58)));
      setDims({ w, h });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const visibleProjects = useMemo(() => projects.slice(0, 24), [projects]);

  const positions = useMemo(
    () => layoutBubblePositions(visibleProjects.length, dims.w, dims.h),
    [visibleProjects.length, dims.w, dims.h],
  );

  const layoutKey = useMemo(
    () =>
      `${visibleProjects.map((p) => p.slug).join('|')}:${dims.w}x${dims.h}:${stageFilter}:${chainSlug}:${timeframe}`,
    [visibleProjects, dims.w, dims.h, stageFilter, chainSlug, timeframe],
  );

  useEffect(() => {
    setOffsets({});
    setDraggingSlug(null);
  }, [layoutKey]);

  const hasCustomLayout = useMemo(
    () => Object.values(offsets).some((o) => Math.abs(o.x) > 1 || Math.abs(o.y) > 1),
    [offsets],
  );

  const handleDragEnd = useCallback((slug: string, delta: { x: number; y: number }) => {
    setDraggingSlug(null);
    if (delta.x === 0 && delta.y === 0) return;
    setOffsets((prev) => ({
      ...prev,
      [slug]: {
        x: (prev[slug]?.x ?? 0) + delta.x,
        y: (prev[slug]?.y ?? 0) + delta.y,
      },
    }));
  }, []);

  return (
    <div className="relative rounded-2xl border border-zinc-800/80 bg-[#030308]">
      {/* Cosmic background — clipped so bubbles can drag outside inner canvas */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl">
        <div
          className="absolute inset-0 opacity-40"
          style={{
            background:
              'radial-gradient(ellipse 80% 60% at 50% 40%, rgba(88,28,135,0.35), transparent 60%), radial-gradient(ellipse 60% 50% at 70% 70%, rgba(16,185,129,0.12), transparent 50%)',
          }}
        />
        <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48Y2lyY2xlIGN4PSIxMCIgY3k9IjEwIiByPSIxIiBmaWxsPSJyZ2JhKDI1NSwyNTUsMjU1LDAuMykiLz48Y2lyY2xlIGN4PSI4MCIgY3k9IjQwIiByPSIwLjUiIGZpbGw9InJnYmEoMjU1LDI1NSwyNTUsMC4yKSIvPjwvc3ZnPg==')] opacity-30" />
      </div>

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

      {/* Legend — outer ring matches bubble border */}
      <div className="relative z-10 flex flex-wrap gap-4 px-4 py-2 text-[10px] uppercase tracking-wider text-zinc-500">
        {(Object.keys(DISCOVER_UNIVERSE_COLORS) as DiscoverUniverseStage[]).map((key) => {
          const meta = DISCOVER_UNIVERSE_COLORS[key];
          return (
            <span key={key} className="flex items-center gap-1.5">
              <span
                className="h-3 w-3 rounded-full border-[2.5px] bg-zinc-950/60"
                style={{ borderColor: meta.border }}
              />
              {meta.label}
            </span>
          );
        })}
        <span className="flex items-center gap-1.5 text-violet-400/90">
          <span className="rounded border border-violet-500/40 px-1.5 py-0.5 text-[9px]">
            {DISCOVER_RECENTLY_LISTED_FILTER_LABEL}
          </span>
          tab = listed ≤14d (ring still follows stage)
        </span>
        <span className="text-zinc-600">· badge = activity score (0–100)</span>
        <span className="flex items-center gap-1.5 text-sky-400/80">
          <span aria-hidden className="text-[11px]">
            ✥
          </span>
          drag bubbles apart to explore
        </span>
        {hasCustomLayout && (
          <button
            type="button"
            onClick={() => setOffsets({})}
            className="rounded-md border border-zinc-700/80 px-2 py-0.5 text-[10px] text-zinc-400 transition hover:border-zinc-500 hover:text-zinc-200"
          >
            Reset layout
          </button>
        )}
      </div>

      {/* Bubble canvas — overflow visible so dragged bubbles stay on screen */}
      <div
        ref={canvasRef}
        className="relative z-[1] w-full select-none overflow-visible"
        style={{ height: dims.h, minHeight: 480, touchAction: 'none' }}
      >
        {visibleProjects.length === 0 ? (
          <p className="flex h-full min-h-[480px] items-center justify-center text-sm text-zinc-500">
            No projects match these filters
          </p>
        ) : (
          visibleProjects.map((p, i) => (
            <DiscoverUniverseBubble
              key={p.slug}
              project={p}
              x={positions[i]?.x ?? dims.w / 2}
              y={positions[i]?.y ?? dims.h / 2}
              index={i}
              offset={offsets[p.slug] ?? { x: 0, y: 0 }}
              canvasRef={canvasRef}
              isDragging={draggingSlug === p.slug}
              onDragStart={() => setDraggingSlug(p.slug)}
              onDragEnd={(delta) => handleDragEnd(p.slug, delta)}
            />
          ))
        )}
      </div>
    </div>
  );
}

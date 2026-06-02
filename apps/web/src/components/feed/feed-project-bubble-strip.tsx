'use client';

import { bubbleRadiusFromActivityScore } from '@dcf/utils';
import type { DiscoverUniverseProject } from '@/lib/api';

export function FeedProjectBubbleStrip({
  projects,
  selectedSlug,
  onSelect,
}: {
  projects: DiscoverUniverseProject[];
  selectedSlug: string | null;
  onSelect: (slug: string | null) => void;
}) {
  const slice = projects.slice(0, 8);

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-min gap-4 px-1">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={`flex shrink-0 flex-col items-center ${!selectedSlug ? 'opacity-100' : 'opacity-60'}`}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-zinc-600 bg-zinc-900 text-xs font-bold text-zinc-400">
            ALL
          </div>
          <span className="mt-1 text-[10px] text-zinc-500">All projects</span>
        </button>
        {slice.map((p) => {
          const size = Math.min(72, bubbleRadiusFromActivityScore(p.activityScore));
          const selected = selectedSlug === p.slug;
          const unread = Math.max(1, Math.round(p.activityScore / 4));
          return (
            <button
              key={p.slug}
              type="button"
              onClick={() => onSelect(selected ? null : p.slug)}
              className={`relative flex shrink-0 flex-col items-center transition ${selected ? 'scale-105 opacity-100' : 'opacity-85 hover:opacity-100'}`}
            >
              <div className="relative">
                {unread > 0 && (
                  <span className="absolute -right-1 -top-1 z-10 flex h-5 min-w-5 items-center justify-center rounded-full bg-violet-600 px-1 text-[9px] font-bold text-white">
                    {unread}
                  </span>
                )}
                <div
                  className="flex items-center justify-center overflow-hidden rounded-full border-2 shadow-lg"
                  style={{
                    width: size,
                    height: size,
                    borderColor: selected ? '#a855f7' : '#52525b',
                    boxShadow: selected ? '0 0 20px rgba(168,85,247,0.4)' : undefined,
                  }}
                >
                  {p.logoUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={p.logoUrl} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <span className="text-xs font-bold">{p.ticker.slice(0, 3)}</span>
                  )}
                </div>
              </div>
              <span className="mt-1 text-xs font-semibold text-white">{p.ticker}</span>
              <span className="flex items-center gap-0.5 text-[10px] text-orange-400">
                🔥 {p.activityScore}
              </span>
            </button>
          );
        })}
        {projects.length > 8 && (
          <div className="flex shrink-0 flex-col items-center justify-center opacity-50">
            <div className="flex h-14 w-14 items-center justify-center rounded-full border border-dashed border-zinc-700 text-xs text-zinc-500">
              +{projects.length - 8}
            </div>
            <span className="mt-1 text-[10px] text-zinc-600">More</span>
          </div>
        )}
      </div>
    </div>
  );
}

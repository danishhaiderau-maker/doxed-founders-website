'use client';

import type { RaiseRoomFilter, RaiseRoomProjectCard } from '@/lib/api';
import { RaiseRoomProjectCardView } from './raise-room-project-card';

const FILTERS: { id: RaiseRoomFilter; label: string }[] = [
  { id: 'trending', label: 'Trending' },
  { id: 'newest', label: 'Newest' },
  { id: 'high_conviction', label: 'High conviction' },
  { id: 'almost_qualified', label: 'Almost qualified' },
  { id: 'ai_picks', label: 'AI picks' },
  { id: 'near_graduation', label: 'Near graduation' },
  { id: 'needs_review', label: 'Needs review' },
];

type Props = {
  filter: RaiseRoomFilter;
  onFilter: (f: RaiseRoomFilter) => void;
  projects: RaiseRoomProjectCard[];
  total: number;
  loading: boolean;
};

export function RaiseRoomDiscoveryFeed({ filter, onFilter, projects, total, loading }: Props) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">Discovery feed</h2>
          <p className="text-sm text-zinc-500">{total} project{total === 1 ? '' : 's'} match this view</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => onFilter(f.id)}
              className={`rounded-full px-3 py-1 text-[11px] font-semibold transition ${
                filter === f.id
                  ? 'bg-amber-500/20 text-amber-100 ring-1 ring-amber-500/40'
                  : 'border border-zinc-700 text-zinc-400 hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <p className="rounded-xl border border-zinc-800 p-8 text-center text-sm text-zinc-500">
          Loading projects…
        </p>
      )}

      {!loading && projects.length === 0 && (
        <p className="rounded-xl border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-500">
          No projects in this filter yet.
        </p>
      )}

      {!loading && projects.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          {projects.map((p) => (
            <RaiseRoomProjectCardView key={p.raiseId} project={p} />
          ))}
        </div>
      )}
    </section>
  );
}

export { RaiseRoomTrendingCards } from './raise-room-project-card';

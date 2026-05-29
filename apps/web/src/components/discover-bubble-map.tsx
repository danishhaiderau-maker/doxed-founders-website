'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import { getStageBucketMeta, type StageBucket } from '@dcf/utils';
import type { DiscoverProject } from '@/lib/api';

export function DiscoverBubbleMap({ projects }: { projects: DiscoverProject[] }) {
  const maxBubble = useMemo(
    () => Math.max(...projects.map((p) => p.bubbleScore), 1),
    [projects],
  );

  return (
    <div className="relative min-h-[460px] rounded-2xl border border-zinc-800 bg-gradient-to-b from-zinc-950/80 to-zinc-900/30 p-6">
      <div className="mb-4 flex flex-wrap gap-3 text-[10px] uppercase tracking-wider text-zinc-500">
        {(['IDEA_STAGE', 'BUILDING', 'LAUNCH_READY', 'LIVE_TOKEN'] as StageBucket[]).map((b) => {
          const meta = getStageBucketMeta(b);
          return (
            <span key={b} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color }} />
              {meta.label}
            </span>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center justify-center gap-5 py-4">
        {projects.slice(0, 20).map((p) => {
          const size = 72 + (p.bubbleScore / maxBubble) * 88;
          const meta = getStageBucketMeta(p.stageBucket as StageBucket);
          return (
            <Link
              key={p.slug}
              href={`/project/${p.slug}`}
              className="group flex flex-col items-center transition hover:scale-105"
              style={{ width: size + 20 }}
            >
              <div
                className="flex items-center justify-center overflow-hidden rounded-full shadow-lg transition group-hover:shadow-emerald-900/30"
                style={{
                  width: size,
                  height: size,
                  border: `3px solid ${meta.border}`,
                  boxShadow: `0 0 24px ${meta.color}33`,
                  background: `radial-gradient(circle at 30% 30%, ${meta.color}44, #09090b)`,
                }}
                title={p.name}
              >
                {p.logoUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={p.logoUrl}
                    alt=""
                    className="h-[85%] w-[85%] rounded-full object-cover"
                  />
                ) : (
                  <span className="text-sm font-bold text-white">{p.ticker.slice(0, 3)}</span>
                )}
              </div>
              <p className="mt-2 max-w-[110px] truncate text-center text-xs font-medium text-zinc-300 group-hover:text-white">
                {p.name}
              </p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

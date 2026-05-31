'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { fetchDemandHeatmap, fetchLatestFounderVideos, FounderVideo } from '@/lib/api';
import { formatUsd } from '@dcf/utils';

function videoTypeLabel(type: string) {
  const map: Record<string, string> = {
    INTRODUCTION: 'Introduction',
    DEEP_DIVE: 'Deep dive',
    MONTHLY_UPDATE: 'Monthly update',
    QA: 'Public Q&A',
  };
  return map[type] ?? type;
}

function timeAgo(iso: string) {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return '1 day ago';
  return `${days} days ago`;
}

export function LatestFounderVideos({ limit = 8 }: { limit?: number }) {
  const [videos, setVideos] = useState<FounderVideo[]>([]);

  useEffect(() => {
    fetchLatestFounderVideos(limit).then(setVideos).catch(() => setVideos([]));
  }, [limit]);

  if (!videos.length) {
    return (
      <section className="border-y border-zinc-800/80 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h2 className="text-2xl font-bold text-white">Latest founder videos</h2>
          <p className="mt-3 text-zinc-500">
            Founders share public video introductions — no passport uploads, just real humans
            explaining their projects.
          </p>
          <Link href="/list-your-project" className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
            List your project with a video →
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section className="border-y border-zinc-800/80 py-16">
      <div className="mx-auto max-w-6xl px-6">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-emerald-500">Trust layer</p>
            <h2 className="mt-2 text-2xl font-bold text-white">Latest founder videos</h2>
            <p className="mt-2 text-sm text-zinc-500">Hear founders explain their ideas — verified presence, not paperwork.</p>
          </div>
          <Link href="/feed" className="text-sm text-emerald-400 hover:underline">
            Build feed →
          </Link>
        </div>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {videos.map((v) => (
            <a
              key={v.id}
              href={v.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-emerald-500/40"
            >
              <div className="flex aspect-video items-center justify-center rounded-xl bg-zinc-950 text-3xl text-emerald-500/80 group-hover:text-emerald-400">
                ▶
              </div>
              <p className="mt-3 text-xs font-medium uppercase tracking-wide text-zinc-500">
                {videoTypeLabel(v.type)}
              </p>
              <p className="mt-1 line-clamp-2 font-semibold text-white">{v.title}</p>
              <p className="mt-2 text-xs text-zinc-500">
                {v.founder.name}
                {v.project ? ` · ${v.project.name}` : ''}
              </p>
              <div className="mt-2 flex items-center justify-between text-xs text-zinc-600">
                <span>{v.durationMin ? `${v.durationMin} min` : 'Video'}</span>
                <span>{timeAgo(v.publishedAt)}</span>
              </div>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}

export function DemandHeatmapSection() {
  const [rows, setRows] = useState<
    { project: { slug: string; name: string; ticker: string }; goalUsd: number; totalDemand: number }[]
  >([]);

  useEffect(() => {
    fetchDemandHeatmap().then(setRows).catch(() => setRows([]));
  }, []);

  if (!rows.length) return null;

  return (
    <section className="py-16">
      <div className="mx-auto max-w-6xl px-6">
        <h2 className="text-2xl font-bold text-white">Simulated demand</h2>
        <p className="mt-2 text-sm text-zinc-500">Virtual capital allocated — proof of demand before real raises.</p>
        <ul className="mt-6 space-y-3">
          {rows.map((r) => (
            <li key={r.project.slug}>
              <Link
                href={`/project/${r.project.slug}`}
                className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900/30 px-4 py-3 hover:border-emerald-500/30"
              >
                <span className="font-medium">{r.project.name}</span>
                <span className="text-emerald-400">{formatUsd(r.totalDemand, 0)} simulated</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export function FounderOsHubTeaser() {
  const { data: session } = useSession();

  return (
    <section className="border-t border-zinc-800/80 bg-zinc-950/50 py-20">
      <div className="mx-auto max-w-6xl px-6 text-center">
        <p className="text-xs font-semibold uppercase tracking-widest text-emerald-500">Founder OS</p>
        <h2 className="mt-3 text-3xl font-bold text-white">Build → translate → publish everywhere</h2>
        <p className="mx-auto mt-4 max-w-2xl text-zinc-400">
          GitHub sync, Founder Copilot, stack integrations, simulated raises, bounties, and
          one-click publish to build feed, X, and your project room — no Telegram chaos.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link
            href="/feed"
            className="rounded-xl bg-emerald-600 px-6 py-3 text-sm font-semibold text-white hover:bg-emerald-500"
          >
            See build feed
          </Link>
          <Link href="/founders" className="rounded-xl border border-zinc-700 px-6 py-3 text-sm text-zinc-300 hover:text-white">
            Browse founders
          </Link>
          {!session && (
            <Link
              href="/login?callbackUrl=/founder-den"
              className="rounded-xl border border-emerald-500/40 px-6 py-3 text-sm text-emerald-200 hover:text-white"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </section>
  );
}

/** @deprecated use FounderOsHubTeaser */
export const FounderDenHubTeaser = FounderOsHubTeaser;

'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { fetchFounderRoom, FounderRoom } from '@/lib/api';
import { FounderPresenceBadge, TrustRing, BuildHeatmap } from '@/components/founder-presence';
import { JOURNEY_STAGES } from '@dcf/utils';

export default function FounderDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const [founder, setFounder] = useState<FounderRoom | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setFounder(await fetchFounderRoom(slug));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Founder not found');
      setFounder(null);
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  const journeyIdx = founder
    ? JOURNEY_STAGES.findIndex((s) => s.key === founder.journeyStage)
    : -1;

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/founders" className="text-xs text-zinc-500 hover:text-white">
              ← All founders
            </Link>
            <SiteBrand className="mt-1 text-sm" />
            <h1 className="text-xl font-bold">{founder?.name ?? 'Founder'}</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        {loading && <p className="text-zinc-500">Loading…</p>}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {founder && (
          <div className="space-y-10">
            <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6 md:p-8">
              <div className="grid gap-8 lg:grid-cols-[1fr_auto]">
                <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
                  <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-zinc-950 text-2xl font-bold text-emerald-400">
                    {founder.photoUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={founder.photoUrl} alt="" className="h-20 w-20 rounded-full object-cover" />
                    ) : (
                      founder.name.slice(0, 1)
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-3xl font-bold">{founder.name}</h2>
                      <FounderPresenceBadge level={founder.presenceLevel} />
                    </div>
                    {founder.bio && (
                      <p className="mt-4 max-w-2xl leading-relaxed text-zinc-400">{founder.bio}</p>
                    )}
                    <div className="mt-4 flex flex-wrap gap-4 text-sm text-zinc-500">
                      <span>{founder.stats.buildPosts} build posts</span>
                      <span>{founder.stats.videos} videos</span>
                      {founder.buildStreakDays > 0 && (
                        <span className="text-emerald-400">{founder.buildStreakDays} day streak</span>
                      )}
                    </div>
                    <div className="mt-4 flex flex-wrap gap-3 text-sm">
                      {founder.linkedInUrl && (
                        <a href={founder.linkedInUrl} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-zinc-700 px-4 py-2 hover:border-emerald-500/50">
                          LinkedIn
                        </a>
                      )}
                      {founder.twitterUrl && (
                        <a href={founder.twitterUrl} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-zinc-700 px-4 py-2 hover:border-emerald-500/50">
                          X
                        </a>
                      )}
                      {founder.githubUrl && (
                        <a href={founder.githubUrl} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-zinc-700 px-4 py-2 hover:border-emerald-500/50">
                          GitHub
                        </a>
                      )}
                      <Link href="/founder-den" className="rounded-lg border border-emerald-500/40 px-4 py-2 text-emerald-300">
                        Founder Den →
                      </Link>
                    </div>
                  </div>
                </div>
                <TrustRing score={founder.reputation.total} breakdown={founder.reputation} />
              </div>

              <div className="mt-8">
                <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">Founder journey</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {JOURNEY_STAGES.map((s, i) => (
                    <span
                      key={s.key}
                      className={`rounded-lg px-2.5 py-1 text-xs ${
                        i <= journeyIdx
                          ? 'bg-emerald-950/50 text-emerald-300 ring-1 ring-emerald-500/30'
                          : 'bg-zinc-900 text-zinc-600'
                      }`}
                    >
                      {s.label}
                    </span>
                  ))}
                </div>
              </div>

              {founder.heatmap.length > 0 && (
                <div className="mt-8">
                  <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">Build activity</p>
                  <div className="mt-3">
                    <BuildHeatmap cells={founder.heatmap} />
                  </div>
                </div>
              )}
            </section>

            {founder.videos.length > 0 && (
              <section>
                <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-zinc-500">
                  Public videos
                </h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  {founder.videos.map((v) => (
                    <a
                      key={v.id}
                      href={v.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-xl border border-zinc-800 p-4 hover:border-emerald-500/40"
                    >
                      <p className="font-medium text-emerald-400">▶ {v.title}</p>
                      <p className="mt-1 text-xs text-zinc-500">{v.type.replace(/_/g, ' ')}</p>
                    </a>
                  ))}
                </div>
              </section>
            )}

            {founder.buildPosts.length > 0 && (
              <section>
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Build log</h3>
                  <Link href="/build-feed" className="text-xs text-emerald-400 hover:underline">
                    Full feed →
                  </Link>
                </div>
                <ul className="space-y-3">
                  {founder.buildPosts.slice(0, 10).map((p) => (
                    <li key={p.id} className="rounded-xl border border-zinc-800 bg-zinc-900/20 p-4">
                      {p.dayNumber != null && (
                        <span className="text-xs text-emerald-500">Day {p.dayNumber}</span>
                      )}
                      <p className="font-medium">{p.headline}</p>
                      <p className="mt-1 text-sm text-zinc-500 line-clamp-2">{p.body}</p>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-zinc-500">
                Projects ({founder.projects.length})
              </h3>
              <div className="grid gap-4 md:grid-cols-2">
                {founder.projects.map((project) => (
                  <ProjectCard key={project.slug} project={project} />
                ))}
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { FounderBadges } from '@/components/founder-badges';
import { ProjectCard } from '@/components/project-card';
import { fetchFounder, FounderDetail } from '@/lib/api';

export default function FounderDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const [founder, setFounder] = useState<FounderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchFounder(slug);
      setFounder(data);
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

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/founders" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← All founders
            </Link>
            <h1 className="text-xl font-bold">{founder?.name ?? 'Founder'}</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        {loading && <p className="text-[var(--color-muted)]">Loading…</p>}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {founder && (
          <div className="space-y-10">
            <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 md:p-8">
              <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
                <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-[var(--color-background)] text-2xl font-bold text-[var(--color-accent)]">
                  {founder.photoUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={founder.photoUrl}
                      alt=""
                      className="h-20 w-20 rounded-full object-cover"
                    />
                  ) : (
                    founder.name.slice(0, 1)
                  )}
                </div>
                <div className="flex-1">
                  <h2 className="text-3xl font-bold">{founder.name}</h2>
                  <FounderBadges verifications={founder.verifications} />
                  {founder.bio && (
                    <p className="mt-4 max-w-2xl leading-relaxed text-[var(--color-muted)]">
                      {founder.bio}
                    </p>
                  )}
                  <div className="mt-4 flex flex-wrap gap-3 text-sm">
                    {founder.linkedInUrl && (
                      <a
                        href={founder.linkedInUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg border border-[var(--color-border)] px-4 py-2 hover:border-[var(--color-accent)]"
                      >
                        LinkedIn
                      </a>
                    )}
                    {founder.twitterUrl && (
                      <a
                        href={founder.twitterUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg border border-[var(--color-border)] px-4 py-2 hover:border-[var(--color-accent)]"
                      >
                        X / Twitter
                      </a>
                    )}
                    {founder.githubUrl && (
                      <a
                        href={founder.githubUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg border border-[var(--color-border)] px-4 py-2 hover:border-[var(--color-accent)]"
                      >
                        GitHub
                      </a>
                    )}
                    {founder.videoUrl && (
                      <a
                        href={founder.videoUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg border border-[var(--color-accent)]/50 px-4 py-2 text-[var(--color-accent)]"
                      >
                        Founder video
                      </a>
                    )}
                  </div>
                </div>
              </div>
            </section>

            <section>
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
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

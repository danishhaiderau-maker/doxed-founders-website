'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { fetchWatchlist, ProjectSummary } from '@/lib/api';

export default function WatchlistPage() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status === 'loading') return;
    if (!token) {
      setLoading(false);
      return;
    }

    fetchWatchlist(token)
      .then((data) => {
        setProjects(data);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token, status]);

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/projects" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Projects
            </Link>
            <h1 className="text-xl font-bold">Your watchlist</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        {!token && status !== 'loading' && (
          <div className="rounded-xl border border-dashed border-[var(--color-border)] p-12 text-center">
            <p className="text-[var(--color-muted)]">
              Sign in to save curated projects and track them here.
            </p>
            <Link
              href="/login?callbackUrl=/watchlist"
              className="mt-4 inline-block rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-medium text-white"
            >
              Sign in
            </Link>
          </div>
        )}

        {loading && <p className="text-sm text-[var(--color-muted)]">Loading watchlist…</p>}
        {error && <p className="text-sm text-red-300">{error}</p>}

        {token && !loading && projects.length === 0 && !error && (
          <div className="rounded-xl border border-dashed border-[var(--color-border)] p-12 text-center text-[var(--color-muted)]">
            No saved projects yet.{' '}
            <Link href="/projects" className="text-[var(--color-accent)] hover:underline">
              Browse projects
            </Link>{' '}
            and tap <strong className="text-white">Save to watchlist</strong> on any project page.
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <ProjectCard key={project.slug} project={project} />
          ))}
        </div>
      </main>
    </div>
  );
}

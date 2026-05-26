'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { fetchProjects, ProjectSummary } from '@/lib/api';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [filter, setFilter] = useState<'all' | 'featured'>('all');
  const [category, setCategory] = useState<string>('all');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchProjects({
        featured: filter === 'featured',
        category: category === 'all' ? undefined : category,
      });
      setProjects(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects');
    } finally {
      setLoading(false);
    }
  }, [filter, category]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (new URLSearchParams(window.location.search).get('featured') === 'true') {
      setFilter('featured');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const categories = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of projects) {
      if (p.category) map.set(p.category.slug, p.category.name);
    }
    return Array.from(map.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [projects]);

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[#050508]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/" className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
              DoxedCryptoFounder
            </Link>
            <h1 className="text-xl font-bold">Curated Projects</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        <p className="max-w-2xl text-[var(--color-muted)]">
          Verified founders, documented teams, and transparent project profiles. Every listing
          meets our doxxed-founder threshold before publication.
        </p>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <div className="flex rounded-lg border border-[var(--color-border)] p-1">
            {(['all', 'featured'] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`rounded-md px-4 py-1.5 text-sm capitalize ${
                  filter === f
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'text-[var(--color-muted)] hover:text-white'
                }`}
              >
                {f}
              </button>
            ))}
          </div>

          {categories.length > 0 && (
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 text-sm text-white"
            >
              <option value="all">All categories</option>
              {categories.map(([slug, name]) => (
                <option key={slug} value={slug}>
                  {name}
                </option>
              ))}
            </select>
          )}

          <Link
            href="/founders"
            className="ml-auto text-sm text-[var(--color-accent)] hover:text-white"
          >
            Browse founders →
          </Link>
        </div>

        {error && (
          <p className="mt-6 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {loading && !error && (
          <p className="mt-10 text-center text-[var(--color-muted)]">Loading projects…</p>
        )}

        {!loading && !error && projects.length === 0 && (
          <p className="mt-10 text-center text-[var(--color-muted)]">No projects match this filter.</p>
        )}

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {projects.map((project) => (
            <ProjectCard key={project.slug} project={project} />
          ))}
        </div>
      </main>
    </div>
  );
}

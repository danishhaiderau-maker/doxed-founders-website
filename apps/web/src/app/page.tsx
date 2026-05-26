'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { fetchFeaturedProjects, ProjectSummary } from '@/lib/api';
import { apiUrl, describeApiTarget } from '@/lib/api-base';

interface HealthResponse {
  status: string;
  timestamp: string;
  services: {
    api: string;
    database: string;
  };
}

export default function HomePage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [featured, setFeatured] = useState<ProjectSummary[]>([]);

  useEffect(() => {
    const target = describeApiTarget();
    fetch(apiUrl('/health'))
      .then((res) => {
        if (!res.ok) throw new Error(`API returned ${res.status} (tried ${target})`);
        return res.json();
      })
      .then(setHealth)
      .catch((err: Error) => setError(`${err.message} (tried ${target})`));

    fetchFeaturedProjects()
      .then(setFeatured)
      .catch(() => setFeatured([]));
  }, []);

  return (
    <main className="min-h-screen">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div>
            <p className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
              Curated Intelligence
            </p>
            <h1 className="text-xl font-semibold tracking-tight">
              DoxedCryptoFounder
            </h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 py-24">
        <div className="max-w-2xl">
          <h2 className="text-4xl font-bold leading-tight tracking-tight md:text-5xl">
            Serious crypto businesses.
            <br />
            <span className="text-[var(--color-accent)]">
              Public founders.
            </span>
          </h2>
          <p className="mt-6 text-lg text-[var(--color-muted)]">
            A premium curated platform for transparent blockchain projects
            with documented teams, real products, and active development.
          </p>
          <div className="mt-8 flex flex-wrap gap-4">
            <Link
              href="/list-your-project"
              className="rounded-lg bg-[var(--color-accent)] px-6 py-3 text-sm font-medium text-white hover:bg-[var(--color-accent-hover)]"
            >
              List your project — free during beta
            </Link>
            <Link
              href="/projects"
              className="rounded-lg border border-[var(--color-accent)]/50 px-6 py-3 text-sm font-medium text-white hover:border-[var(--color-accent)]"
            >
              Browse curated projects
            </Link>
            <Link
              href="/feed"
              className="rounded-lg border border-emerald-500/40 px-6 py-3 text-sm font-medium text-emerald-300 hover:border-emerald-400 hover:text-white"
            >
              Trading feed — discuss trades
            </Link>
            <Link
              href="/paper-trading"
              className="rounded-lg border border-[var(--color-accent)]/50 px-6 py-3 text-sm font-medium text-white hover:border-[var(--color-accent)]"
            >
              Paper trade any token — $10,000
            </Link>
            <Link
              href="/admin/applications"
              className="rounded-lg border border-[var(--color-border)] px-6 py-3 text-sm font-medium text-[var(--color-muted)] hover:border-[var(--color-accent)] hover:text-white"
            >
              Admin review (sign in)
            </Link>
          </div>
          <p className="mt-4 text-sm text-[var(--color-muted)]">
            Curated listings require 2+ founder proof points. Save projects to your watchlist when
            signed in. Paper trading supports any DexScreener pool link (live prices via DexScreener or GeckoTerminal).
          </p>
        </div>

        {featured.length > 0 && (
          <section className="mt-16">
            <div className="mb-4 flex items-end justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold uppercase tracking-widest text-amber-300/90">
                  Featured
                </h3>
                <p className="mt-1 text-lg font-semibold">Curated doxxed-founder projects</p>
              </div>
              <Link
                href="/projects?featured=true"
                className="shrink-0 text-sm text-[var(--color-accent)] hover:underline"
              >
                View all →
              </Link>
            </div>
            <div className="flex gap-4 overflow-x-auto pb-2">
              {featured.map((project) => (
                <div key={project.slug} className="w-[min(100%,320px)] shrink-0">
                  <ProjectCard project={project} />
                </div>
              ))}
            </div>
          </section>
        )}

        <div className="mt-16 grid gap-4 sm:grid-cols-3">
          {[
            { label: 'Supported Chains', value: '8' },
            { label: 'Paper Trading', value: '$10,000' },
            { label: 'Watchlist', value: 'Save & track' },
          ].map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6"
            >
              <p className="text-2xl font-semibold">{stat.value}</p>
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                {stat.label}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-8 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
          <p className="text-sm font-medium text-[var(--color-muted)]">
            System Status
          </p>
          {error && (
            <p className="mt-2 text-sm text-[var(--color-danger)]">
              API unreachable: {error}. Ensure the Nest API is running on port 4000 (run{' '}
              <code className="text-xs">dev-lan.cmd</code>).
            </p>
          )}
          {health && (
            <div className="mt-3 flex flex-wrap gap-4 text-sm">
              <StatusBadge label="API" status={health.services.api} />
              <StatusBadge
                label="Database"
                status={health.services.database}
              />
              <span className="text-[var(--color-muted)]">
                {health.timestamp}
              </span>
            </div>
          )}
          {!health && !error && (
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              Checking services…
            </p>
          )}
        </div>
      </section>
    </main>
  );
}

function StatusBadge({ label, status }: { label: string; status: string }) {
  const ok = status === 'ok';
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`h-2 w-2 rounded-full ${ok ? 'bg-[var(--color-success)]' : 'bg-[var(--color-danger)]'}`}
      />
      {label}: {status}
    </span>
  );
}

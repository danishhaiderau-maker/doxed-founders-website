'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { FounderBadges } from '@/components/founder-badges';
import { fetchFounders, FounderSummary } from '@/lib/api';

export default function FoundersIndexPage() {
  const [founders, setFounders] = useState<FounderSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchFounders()
      .then(setFounders)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/projects" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Projects
            </Link>
            <h1 className="text-xl font-bold">Public Founders</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        <p className="max-w-2xl text-[var(--color-muted)]">
          Identified leadership behind curated listings — with verification badges and linked
          project portfolios.
        </p>

        {error && (
          <p className="mt-6 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {loading && <p className="mt-10 text-[var(--color-muted)]">Loading founders…</p>}

        <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {founders.map((founder) => (
            <Link
              key={founder.slug}
              href={`/founder/${founder.slug}`}
              className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 transition hover:border-[var(--color-accent)]/50"
            >
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-background)] text-lg font-bold text-[var(--color-accent)]">
                  {founder.photoUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={founder.photoUrl} alt="" className="h-12 w-12 rounded-full object-cover" />
                  ) : (
                    founder.name.slice(0, 1)
                  )}
                </div>
                <div>
                  <h2 className="font-semibold">{founder.name}</h2>
                  <p className="text-xs text-[var(--color-muted)]">
                    {founder.projectCount} project{founder.projectCount === 1 ? '' : 's'}
                  </p>
                </div>
              </div>
              {founder.bio && (
                <p className="mt-3 line-clamp-3 text-sm text-[var(--color-muted)]">{founder.bio}</p>
              )}
              <FounderBadges verifications={founder.verifications} />
            </Link>
          ))}
        </div>
      </main>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useState } from 'react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { createBuildPost } from '@/lib/api';
import { JOURNEY_STAGES } from '@dcf/utils';

export default function FounderDenPage() {
  const { data: session } = useSession();
  const [headline, setHeadline] = useState('');
  const [body, setBody] = useState('');
  const [dayNumber, setDayNumber] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submitPost(e: React.FormEvent) {
    e.preventDefault();
    if (!session?.accessToken) return;
    setLoading(true);
    setError(null);
    try {
      const result = await createBuildPost(
        {
          headline: headline.trim(),
          body: body.trim(),
          dayNumber: dayNumber ? Number(dayNumber) : undefined,
        },
        session.accessToken,
      );
      setMessage(`Build post published! Streak: ${result.buildStreakDays ?? 1} days`);
      setHeadline('');
      setBody('');
      setDayNumber('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not publish');
    } finally {
      setLoading(false);
    }
  }

  if (!session) {
    return (
      <main className="min-h-screen bg-[#050508] px-6 py-20">
        <div className="mx-auto max-w-lg text-center">
          <h1 className="text-2xl font-bold">Founder Den</h1>
          <p className="mt-4 text-zinc-400">Sign in to access your founder operating system.</p>
          <Link
            href="/login?callbackUrl=/founder-den"
            className="mt-6 inline-block rounded-xl bg-emerald-600 px-6 py-3 font-semibold text-white"
          >
            Sign in
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Founder Den</h1>
            <p className="text-xs text-zinc-500">Build publicly · earn trust · validate demand</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-8 px-6 py-8">
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
          <h2 className="text-lg font-semibold">Welcome to Founder Den</h2>
          <p className="mt-2 text-sm text-zinc-400">
            No passport uploads. Verify through <strong className="text-white">public presence</strong>:
            videos, build logs, GitHub, and community engagement.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['🟡 Public Founder', 'Video introduction'],
              ['🟢 Verified Builder', 'Build logs + GitHub'],
              ['🔵 Transparent Founder', 'Q&A + roadmap'],
              ['🟣 Proven Founder', 'Shipped products'],
            ].map(([title, desc]) => (
              <div key={title} className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4 text-sm">
                <p className="font-medium text-white">{title}</p>
                <p className="mt-1 text-xs text-zinc-500">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
          <h2 className="text-lg font-semibold">Founder journey</h2>
          <div className="mt-4 flex flex-wrap gap-2">
            {JOURNEY_STAGES.map((s, i) => (
              <span key={s.key} className="flex items-center gap-1 text-xs text-zinc-500">
                <span className="rounded bg-zinc-800 px-2 py-1 text-zinc-300">{s.label}</span>
                {i < JOURNEY_STAGES.length - 1 && <span>→</span>}
              </span>
            ))}
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
            <h2 className="font-semibold">Post a build update</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Requires your account linked to a founder profile (contact admin after listing approval).
            </p>
            <form onSubmit={submitPost} className="mt-4 space-y-3">
              <input
                value={headline}
                onChange={(e) => setHeadline(e.target.value)}
                placeholder="Headline — e.g. Wallet integration complete"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm outline-none focus:border-emerald-500/50"
                required
              />
              <input
                type="number"
                value={dayNumber}
                onChange={(e) => setDayNumber(e.target.value)}
                placeholder="Day number (optional)"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm outline-none focus:border-emerald-500/50"
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={4}
                placeholder="What did you ship? What's next?"
                className="w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm outline-none focus:border-emerald-500/50"
                required
              />
              {error && <p className="text-sm text-red-300">{error}</p>}
              {message && <p className="text-sm text-emerald-300">{message}</p>}
              <button
                type="submit"
                disabled={loading}
                className="rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {loading ? 'Publishing…' : 'Publish build update'}
              </button>
            </form>
          </div>

          <div className="space-y-4">
            <Link
              href="/list-your-project"
              className="block rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-5 hover:border-emerald-500/50"
            >
              <p className="font-semibold text-emerald-200">List your project</p>
              <p className="mt-1 text-sm text-zinc-400">Add a public video URL — no sensitive documents.</p>
            </Link>
            <Link
              href="/build-feed"
              className="block rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5 hover:border-zinc-700"
            >
              <p className="font-semibold">View build feed</p>
              <p className="mt-1 text-sm text-zinc-500">See what founders are shipping in public.</p>
            </Link>
            <Link
              href="/founders"
              className="block rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5 hover:border-zinc-700"
            >
              <p className="font-semibold">Browse founder profiles</p>
              <p className="mt-1 text-sm text-zinc-500">Reputation, videos, and project rooms.</p>
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}

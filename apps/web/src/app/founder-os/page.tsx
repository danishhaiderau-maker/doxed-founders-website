'use client';

import { Suspense } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderOsShell } from '@/components/founder-os/founder-os-shell';

export default function FounderOsPage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Founder OS</h1>
            <p className="text-xs text-zinc-500">
              The operating system for building companies · v0.1 kernel
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6 flex flex-wrap gap-4 text-sm">
          <Link href="/founder-os/decisions" className="text-violet-400 hover:underline">
            Decision Log →
          </Link>
          <Link href="/settings/ai-usage" className="text-zinc-500 hover:text-white">
            AI Usage →
          </Link>
          <Link href="/founder-den" className="text-zinc-500 hover:text-white">
            Founder Den →
          </Link>
        </div>

        {!session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/founder-os" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to open your Founder OS workspace.
          </div>
        ) : (
          <Suspense fallback={<p className="text-sm text-zinc-500">Loading workspace…</p>}>
            <FounderOsShell accessToken={session.accessToken} />
          </Suspense>
        )}
      </div>
    </main>
  );
}

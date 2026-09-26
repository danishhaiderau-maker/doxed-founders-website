'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { NucleusPanel } from '@/components/nucleus/nucleus-panel';

export default function FounderIdeNucleusPage() {
  const { data: session, status } = useSession();

  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white">Nucleus</h1>
            <p className="text-sm text-zinc-400">
              Founder Graph. Click a node to ask the Founder OS gateway (Auto).
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-4">
          <Link href="/founder-ide" className="text-xs text-zinc-500 hover:text-white">
            ← Founder IDE
          </Link>
        </div>

        {status === 'loading' && <p className="text-sm text-zinc-500">Checking session…</p>}

        {status !== 'loading' && !session?.accessToken && (
          <div className="rounded-2xl border border-amber-500/30 bg-amber-950/20 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/founder-ide/nucleus" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to load your Founder Graph. This page uses your session JWT. The IDE extension uses a
            Founder Node token on the same <code className="text-amber-50">GET /api/ide/nucleus</code>{' '}
            route.
          </div>
        )}

        {session?.accessToken && <NucleusPanel accessToken={session.accessToken} />}
      </div>
    </main>
  );
}

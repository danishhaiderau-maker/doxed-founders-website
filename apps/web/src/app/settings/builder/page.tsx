'use client';

import Link from 'next/link';
import { Suspense } from 'react';
import { useSession } from 'next-auth/react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { BuilderSettingsPanel } from '@/components/settings/builder-settings-panel';

export default function BuilderSettingsPage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Integrations</h1>
            <p className="text-sm text-zinc-500">
              Downloads &amp; pairing · AI providers · Infrastructure · Security
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        <div className="mb-6 flex flex-wrap gap-4 text-sm">
          <Link href="/downloads" className="text-emerald-400 hover:underline">
            All downloads →
          </Link>
          <Link href="/settings/security" className="text-zinc-500 hover:text-white">
            Security →
          </Link>
          <Link href="/founder-den?tab=build" className="text-emerald-400 hover:underline">
            ← Founder Copilot
          </Link>
        </div>

        {!session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/settings/builder" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to configure your builder stack.
          </div>
        ) : (
          <Suspense fallback={<p className="text-sm text-zinc-500">Loading integrations…</p>}>
            <BuilderSettingsPanel accessToken={session.accessToken} />
          </Suspense>
        )}
      </div>
    </main>
  );
}

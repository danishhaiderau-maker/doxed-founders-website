'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AiProxyDashboard } from '@/components/settings/ai-proxy-dashboard';

export default function AiUsagePage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">AI Usage</h1>
            <p className="text-sm text-zinc-500">
              Founder OS proxy spend · DDollar burn · Cursor Pro saved
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        <div className="mb-6 flex flex-wrap gap-4 text-sm">
          <Link href="/settings/builder?tab=ai" className="text-violet-300 hover:underline">
            ← AI providers
          </Link>
          <Link href="/settings/builder?tab=founder-node" className="text-zinc-500 hover:text-white">
            Founder Node →
          </Link>
        </div>

        {!session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/settings/ai-usage" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to view your AI proxy usage.
          </div>
        ) : (
          <AiProxyDashboard accessToken={session.accessToken} />
        )}
      </div>
    </main>
  );
}

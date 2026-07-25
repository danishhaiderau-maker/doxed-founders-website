'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { LogIn } from 'lucide-react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderOsShell } from '@/components/founder-os/founder-os-shell';

export default function FounderOsPage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-4 sm:px-6">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-2 text-2xl font-semibold text-white">Founder workspace</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Projects, AI, services, and desktop control in one focused view.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-5 py-8 sm:px-6">
        {!session?.accessToken ? (
          <div className="flex min-h-48 items-center justify-center border-y border-zinc-800 py-10">
            <div className="max-w-md text-center">
              <h2 className="text-xl font-semibold text-white">Open your Founder workspace</h2>
              <p className="mt-2 text-sm leading-6 text-zinc-400">
                Sign in with the same Doxxed account used by Founder IDE and Founder Node.
              </p>
              <Link
                href="/login?callbackUrl=/founder-os"
                className="mt-5 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-500"
              >
                <LogIn className="h-4 w-4" aria-hidden />
                Sign in
              </Link>
            </div>
          </div>
        ) : (
          <Suspense fallback={<p className="text-sm text-zinc-500">Loading workspace...</p>}>
            <FounderOsShell accessToken={session.accessToken} />
          </Suspense>
        )}
      </div>
    </main>
  );
}

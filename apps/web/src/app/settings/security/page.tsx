'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { SecuritySettingsPanel } from '@/components/settings/security-settings-panel';

export default function SecuritySettingsPage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Account security</h1>
            <p className="text-sm text-zinc-500">2FA · Passkeys · Wallet · Recovery codes</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        {!session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/settings/security" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to manage security settings.
          </div>
        ) : (
          <>
            <Link href="/founder-den" className="mb-6 inline-block text-sm text-emerald-400 hover:underline">
              ← Back to Founder Workspace
            </Link>
            <SecuritySettingsPanel accessToken={session.accessToken} />
          </>
        )}
      </div>
    </main>
  );
}

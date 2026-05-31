'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AccountHub, type AccountTab } from '@/components/account/account-hub';

const VALID_TABS = new Set<AccountTab>([
  'overview',
  'topup',
  'security',
  'notifications',
  'connected',
  'points',
  'reputation',
  'activity',
]);

function AccountPageInner() {
  const searchParams = useSearchParams();
  const tabParam = searchParams.get('tab') ?? 'overview';
  const initialTab = VALID_TABS.has(tabParam as AccountTab)
    ? (tabParam as AccountTab)
    : 'overview';

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Account</h1>
            <p className="text-sm text-zinc-500">
              Overview · Security · Notifications · DDollar · Reputation
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-8">
        <AccountHub initialTab={initialTab} />
      </div>
    </main>
  );
}

export default function AccountPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508]" />}>
      <AccountPageInner />
    </Suspense>
  );
}

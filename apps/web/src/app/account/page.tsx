'use client';

import { useSearchParams, redirect } from 'next/navigation';
import { Suspense } from 'react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AccountHub, type AccountTab } from '@/components/account/account-hub';

const TAB_ALIASES: Record<string, AccountTab> = {
  profile: 'profile',
  overview: 'profile',
  reputation: 'profile',
  security: 'security',
  connected: 'security',
  plan: 'plan',
  topup: 'plan',
  inbox: 'inbox',
  messages: 'inbox',
  notifications: 'inbox',
  activity: 'inbox',
};

function AccountPageInner() {
  const searchParams = useSearchParams();
  const tabParam = searchParams.get('tab') ?? 'profile';

  if (tabParam === 'points') {
    redirect('/ddollar');
  }

  const initialTab = TAB_ALIASES[tabParam] ?? 'profile';
  const messageWith = searchParams.get('with');

  return (
    <main className="min-h-screen bg-[#0b0c0e]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Account</h1>
            <p className="text-sm text-zinc-500">
              Profile, security, plan, and history in one place.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-8">
        <AccountHub
          initialTab={initialTab}
          initialMessageWithUserId={messageWith}
        />
      </div>
    </main>
  );
}

export default function AccountPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#0b0c0e]" />}>
      <AccountPageInner />
    </Suspense>
  );
}

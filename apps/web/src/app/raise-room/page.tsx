'use client';

import { Suspense } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { RaiseRoomDiscoveryHub } from '@/components/raise-room/raise-room-discovery-hub';
import { LaunchTeaserCard } from '@/components/raise-room/launch-teaser-card';

export default function RaiseRoomPage() {
  const { data: session } = useSession();

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-5 sm:px-6">
          <div>
            <SiteBrand className="text-sm" />
            <p className="mt-1 text-xs text-zinc-500">
              Discovery · Validation · Paper conviction · Token launch
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-6">
        {session?.accessToken && (
          <Suspense fallback={null}>
            <LaunchTeaserCard accessToken={session.accessToken} />
          </Suspense>
        )}
        <RaiseRoomDiscoveryHub />
      </div>
    </main>
  );
}

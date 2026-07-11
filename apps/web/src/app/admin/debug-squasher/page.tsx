'use client';

import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { SiteNav } from '@/components/site-nav';
import { DebugSquasherPanel } from '@/components/debug-squasher/debug-squasher-panel';

/**
 * Admin page for the Debug Squasher. Wraps the shared panel component with
 * the standard Founder OS auth gate + site chrome.
 *
 * Mounted at /admin/debug-squasher.
 */
export default function AdminDebugSquasherPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const isAdmin = session?.user?.role === 'ADMIN';

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/debug-squasher');
      return;
    }
    if (!isAdmin) {
      router.replace('/');
    }
  }, [status, isAdmin, router]);

  if (status === 'loading' || !isAdmin) {
    return null;
  }

  return (
    <>
      <SiteNav />
      <main className="mx-auto max-w-5xl px-4 py-8">
        <DebugSquasherPanel />
      </main>
    </>
  );
}

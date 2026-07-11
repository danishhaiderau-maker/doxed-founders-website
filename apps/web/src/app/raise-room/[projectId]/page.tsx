'use client';

import { Suspense } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { TokenLaunchPanel } from '@/components/raise-room/token-launch-panel';

/**
 * /raise-room/[projectId] — the per-project Raise Room launch surface.
 *
 * This is where a founder manages their token launch (eligibility, release,
 * 15-day window) and where scouts pledge DDollar toward the 100K threshold.
 * The discovery hub at /raise-room shows the feed of all projects; this is
 * the deep-dive for one.
 */
export default function RaiseRoomProjectPage() {
  const { data: session } = useSession();
  const params = useParams<{ projectId: string }>();
  const projectId = params?.projectId;

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <p className="mt-1 text-xs text-zinc-500">
              Raise Room · Token Launch
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6 flex flex-wrap gap-4 text-sm">
          <Link href="/raise-room" className="text-violet-400 hover:underline">
            ← Back to Raise Room
          </Link>
        </div>

        {!projectId ? (
          <div className="rounded-xl border border-red-500/30 bg-red-950/20 p-6 text-sm text-red-200">
            Missing project ID.
          </div>
        ) : !session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link
              href={`/login?callbackUrl=/raise-room/${projectId}`}
              className="font-semibold underline"
            >
              Sign in
            </Link>{' '}
            to pledge DDollar or release your token.
          </div>
        ) : (
          <Suspense
            fallback={
              <p className="text-sm text-zinc-500">Loading launch panel…</p>
            }
          >
            <TokenLaunchPanel
              projectId={projectId}
              accessToken={session.accessToken}
            />
          </Suspense>
        )}
      </div>
    </main>
  );
}

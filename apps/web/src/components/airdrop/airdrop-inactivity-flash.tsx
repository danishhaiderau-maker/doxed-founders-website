'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { fetchAirdropRunwayMe } from '@/lib/api';
import { isHubWorkspacePath } from '@/components/hub-nav-config';
import { usePathname } from 'next/navigation';

/** System flash when user is on airdrop decay path (3+ weeks idle). */
export function AirdropInactivityFlash() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const [warning, setWarning] = useState<{ message: string; level: string } | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!session?.accessToken || !isHubWorkspacePath(pathname ?? '')) {
      setWarning(null);
      return;
    }
    fetchAirdropRunwayMe(session.accessToken)
      .then((me) => setWarning(me.warning))
      .catch(() => setWarning(null));
  }, [session?.accessToken, pathname]);

  if (!warning || dismissed) return null;

  return (
    <div
      className={`fixed bottom-4 left-4 right-4 z-[90] mx-auto max-w-lg rounded-xl border px-4 py-3 shadow-xl sm:left-auto sm:right-6 ${
        warning.level === 'critical'
          ? 'border-red-500/50 bg-red-950/95'
          : 'border-amber-500/40 bg-amber-950/95'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-white">Airdrop runway</p>
          <p className="mt-1 text-xs text-zinc-200">{warning.message}</p>
          <Link href="/airdrop" className="mt-2 inline-block text-[11px] font-semibold text-cyan-300 underline">
            View runway →
          </Link>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="shrink-0 text-zinc-400 hover:text-white"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

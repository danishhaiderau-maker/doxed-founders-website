'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { fetchBuilderRewardsMe } from '@/lib/api';

/** Hub banner when Builder Score is decaying from inactivity. */
export function BuilderRewardsFlash() {
  const { data: session } = useSession();
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.accessToken) {
      setMessage(null);
      return;
    }
    fetchBuilderRewardsMe(session.accessToken)
      .then((me) => {
        if (me.warning?.level === 'critical') setMessage(me.warning.message);
        else setMessage(null);
      })
      .catch(() => setMessage(null));
  }, [session?.accessToken]);

  if (!message) return null;

  return (
    <div className="border-b border-amber-500/30 bg-amber-950/40 px-4 py-2">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-white">Builder Rewards</p>
          <p className="text-[11px] text-amber-100/90">{message}</p>
        </div>
        <Link href="/builder-rewards" className="text-[11px] font-semibold text-cyan-300 underline">
          View rewards →
        </Link>
      </div>
    </div>
  );
}

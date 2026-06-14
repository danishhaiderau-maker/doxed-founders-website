'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { formatDdollarCompact } from '@dcf/utils';
import { fetchAccountOverview } from '@/lib/api';

/** Live DDollar balance for sidebars — never hardcode mock values. */
export function DdollarBalanceSidebar() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [balance, setBalance] = useState<number | null>(null);

  useEffect(() => {
    if (!token) {
      setBalance(null);
      return;
    }
    let cancelled = false;
    fetchAccountOverview(token)
      .then((ov) => {
        if (!cancelled) setBalance(ov.reputation.reputationPoints);
      })
      .catch(() => {
        if (!cancelled) setBalance(null);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (!token) {
    return (
      <Link href="/login?callbackUrl=/ddollar" className="mt-1 block text-xs text-violet-300 hover:underline">
        Sign in to view DDollar
      </Link>
    );
  }

  if (balance == null) {
    return <p className="mt-1 text-sm text-zinc-500">Loading wallet…</p>;
  }

  return (
    <Link href="/ddollar" className="mt-1 block">
      <span className="text-lg font-bold text-emerald-400">{formatDdollarCompact(balance)}</span>
      <span className="mt-0.5 block text-xs text-zinc-500">Tap to open wallet</span>
    </Link>
  );
}

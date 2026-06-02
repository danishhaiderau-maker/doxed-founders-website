'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { formatPercent } from '@dcf/utils';
import { fetchReputationMe } from '@/lib/api';

export function DiscoverBottomCtas({ scoutCount }: { scoutCount: number }) {
  const { data: session } = useSession();
  const [airdropPct, setAirdropPct] = useState<number | null>(null);

  useEffect(() => {
    if (!session?.accessToken) return;
    fetchReputationMe(session.accessToken)
      .then((me) => setAirdropPct(me.supplyPercent))
      .catch(() => setAirdropPct(null));
  }, [session?.accessToken]);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Link
        href="/list-your-project"
        className="group rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-5 transition hover:border-emerald-500/40"
      >
        <p className="text-xs font-semibold uppercase tracking-wider text-emerald-500/80">
          Apply For Listing
        </p>
        <p className="mt-2 text-sm text-zinc-400">
          For doxxed founders only. Launch with trust.
        </p>
        <span className="mt-3 inline-block text-sm font-semibold text-emerald-300 group-hover:underline">
          Apply now →
        </span>
      </Link>

      <Link
        href="/predict"
        className="group rounded-xl border border-violet-500/25 bg-violet-950/15 p-5 transition hover:border-violet-500/40"
      >
        <p className="text-xs font-semibold uppercase tracking-wider text-violet-400">
          Scout &amp; Earn DDollar
        </p>
        <p className="mt-2 text-sm text-zinc-400">
          Review projects. Protect the community.
        </p>
        <span className="mt-3 inline-flex items-center gap-2 text-sm font-semibold text-violet-200 group-hover:underline">
          Start scouting
          <span className="rounded-full bg-violet-500/30 px-2 py-0.5 text-xs">{scoutCount}</span>
        </span>
      </Link>

      <div className="rounded-xl border border-sky-500/25 bg-sky-950/15 p-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-sky-400">
          DDollar Airdrop Share
        </p>
        <p className="mt-2 text-2xl font-bold tabular-nums text-white">
          {airdropPct != null ? formatPercent(airdropPct, 2) : '—'}
        </p>
        <p className="mt-1 text-xs text-zinc-500">
          Future distribution depends on ecosystem DDollar participation.
        </p>
        <Link href="/ddollar" className="mt-3 inline-block text-sm text-sky-300 hover:underline">
          Learn more →
        </Link>
      </div>
    </div>
  );
}

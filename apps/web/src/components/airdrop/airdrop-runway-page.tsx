'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import {
  AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY,
  AIRDROP_INACTIVITY_WARN_DAYS,
  AIRDROP_POOL_USD,
  AIRDROP_SUPPLY_PERCENT,
  formatTokenAmount,
  formatUsd,
} from '@dcf/utils';
import {
  fetchAirdropRunway,
  fetchAirdropRunwayMe,
  type AirdropRunwayEntry,
  type AirdropRunwayResponse,
} from '@/lib/api';
import { TwitterIdentityLink } from '@/components/account/twitter-identity-link';

function statusBadge(status: AirdropRunwayEntry['status']) {
  switch (status) {
    case 'active':
      return 'bg-emerald-500/20 text-emerald-200';
    case 'warming':
      return 'bg-sky-500/20 text-sky-200';
    case 'at_risk':
      return 'bg-amber-500/20 text-amber-200';
    case 'decaying':
      return 'bg-red-500/25 text-red-200';
    default:
      return 'bg-zinc-700 text-zinc-300';
  }
}

function eligibilityBadge(x: AirdropRunwayEntry['xEligibility']) {
  switch (x) {
    case 'eligible':
      return 'text-emerald-300';
    case 'review':
      return 'text-amber-300';
    default:
      return 'text-zinc-500';
  }
}

export function AirdropRunwayPage() {
  const { data: session } = useSession();
  const [board, setBoard] = useState<AirdropRunwayResponse | null>(null);
  const [me, setMe] = useState<Awaited<ReturnType<typeof fetchAirdropRunwayMe>> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchAirdropRunway(100)
      .then(setBoard)
      .catch((e: Error) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (!session?.accessToken) {
      setMe(null);
      return;
    }
    fetchAirdropRunwayMe(session.accessToken)
      .then(setMe)
      .catch(() => setMe(null));
  }, [session?.accessToken]);

  return (
    <div className="space-y-8">
      <div className="rounded-2xl border border-cyan-500/30 bg-gradient-to-br from-cyan-950/40 to-violet-950/20 p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-400">Community airdrop</p>
        <h2 className="mt-2 text-2xl font-bold text-white">Airdrop Runway</h2>
        <p className="mt-2 max-w-2xl text-sm text-zinc-400">
          Live leaderboard of <strong className="text-zinc-200">X-connected</strong> accounts on track to claim
          from the {AIRDROP_SUPPLY_PERCENT}% community pool (~{formatUsd(AIRDROP_POOL_USD, 0)} at launch). Daily
          activity decides human vs bot — not commit counts.
        </p>
      </div>

      {session && me?.warning && (
        <div
          className={`rounded-xl border p-4 ${
            me.warning.level === 'critical'
              ? 'border-red-500/50 bg-red-950/30'
              : 'border-amber-500/40 bg-amber-950/25'
          }`}
        >
          <p className="text-sm font-semibold text-white">System warning</p>
          <p className="mt-1 text-xs text-zinc-300">{me.warning.message}</p>
        </div>
      )}

      {!session && (
        <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4 text-sm text-amber-100">
          <Link href="/login?callbackUrl=%2Fairdrop" className="font-semibold text-cyan-300 underline">
            Sign in with X (Twitter)
          </Link>{' '}
          to appear on the runway. Google-only accounts are not eligible for the community airdrop list.
        </div>
      )}

      {session && me?.needsTwitter && (
        <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4 text-sm text-amber-100">
          Connect X in{' '}
          <Link href="/account?tab=connected" className="text-cyan-300 underline">
            Connected Accounts
          </Link>{' '}
          to join the runway.
        </div>
      )}

      {session && me && !me.needsTwitter && me.rank != null && (
        <div className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-4">
          <p className="text-xs text-violet-200/90">Your runway position</p>
          <p className="mt-1 text-lg font-bold text-white">
            #{me.rank} · Human score {me.humanScore}% · ~{formatTokenAmount(me.estimatedTokens)} tokens (
            {formatUsd(me.estimatedUsd, 0)})
          </p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h3 className="text-sm font-semibold text-white">Eligibility rules</h3>
          <ul className="mt-3 space-y-2 text-xs text-zinc-400">
            <li>Must sign in with a linked <strong className="text-zinc-200">X (Twitter)</strong> account.</li>
            <li>
              Listing today is <strong className="text-zinc-200">not a guarantee</strong> — final cuts happen at
              airdrop time (bots, fake tickers, sybil clusters).
            </li>
            <li>
              <strong className="text-zinc-200">Blue tick not required.</strong> If you ever had X Premium, we may
              boost trust when verifiable. Can&apos;t afford Blue? Activity is your proof-of-human.
            </li>
            <li>
              Inactive {AIRDROP_INACTIVITY_WARN_DAYS}+ days: up to{' '}
              <strong className="text-amber-200">{AIRDROP_INACTIVITY_DECAY_DDOLLAR_PER_DAY} DDollar/day</strong>{' '}
              redirected to active traders at snapshot (paper wallet).
            </li>
          </ul>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h3 className="text-sm font-semibold text-white">Level up (gamified)</h3>
          <ul className="mt-3 space-y-2 text-xs text-zinc-400">
            <li>
              <strong className="text-emerald-300">Proof-of-human streak</strong> — trade, comment, or vote every
              48h to keep Human score high.
            </li>
            <li>
              <strong className="text-cyan-300">Conviction multiplier</strong> — verified paper trades with thesis
              weigh more than idle lurkers.
            </li>
            <li>
              <strong className="text-violet-300">Scout jury slot</strong> — helpful marks and trust votes protect
              you from bot-only filters.
            </li>
            <li>
              <strong className="text-amber-300">Runway boost</strong> — top Human + Reputation scores surface first
              for scouts and investors watching the list.
            </li>
          </ul>
        </div>
      </div>

      {err && <p className="text-sm text-red-300">{err}</p>}

      {board && (
        <>
          <p className="text-xs text-zinc-500">
            {board.twitterConnectedCount} X-linked accounts listed · {board.totalListed} scanned · pool split by
            reputation + activity
          </p>
          <div className="overflow-x-auto rounded-xl border border-zinc-800">
            <table className="w-full min-w-[720px] text-left text-xs">
              <thead className="border-b border-zinc-800 bg-zinc-900/80 text-[10px] uppercase tracking-wider text-zinc-500">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2">X</th>
                  <th className="px-3 py-2">Human</th>
                  <th className="px-3 py-2">Activity</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Est. claim</th>
                </tr>
              </thead>
              <tbody>
                {board.entries.map((row) => (
                  <tr
                    key={row.userId}
                    className={`border-b border-zinc-800/80 ${
                      session?.user?.id === row.userId ? 'bg-violet-950/30' : ''
                    }`}
                  >
                    <td className="px-3 py-2 font-mono text-zinc-400">{row.rank}</td>
                    <td className="px-3 py-2 font-medium text-white">{row.displayName}</td>
                    <td className="px-3 py-2">
                      {row.twitterHandle ? (
                        <TwitterIdentityLink handle={row.twitterHandle} showLabel={false} />
                      ) : (
                        <span className="text-zinc-600">—</span>
                      )}
                      <p className={`mt-0.5 text-[10px] ${eligibilityBadge(row.xEligibility)}`}>
                        {row.xEligibility}
                      </p>
                    </td>
                    <td className="px-3 py-2 text-zinc-300">{row.humanScore}%</td>
                    <td className="px-3 py-2 text-zinc-400">{row.activityScore}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${statusBadge(row.status)}`}>
                        {row.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-emerald-300/90">
                      {formatTokenAmount(row.estimatedTokens)}
                      <span className="block text-[10px] text-zinc-500">{formatUsd(row.estimatedUsd, 0)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-zinc-600">{board.rules.snapshotNote}</p>
        </>
      )}
    </div>
  );
}

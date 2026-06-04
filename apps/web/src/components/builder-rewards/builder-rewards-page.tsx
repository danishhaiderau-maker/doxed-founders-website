'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import {
  AIRDROP_POOL_USD,
  AIRDROP_SUPPLY_PERCENT,
  AIRDROP_TOKEN_POOL,
  BUILDER_REWARDS_SNAPSHOT_WEIGHTS,
  LAUNCH_FDV_USD,
  TOKEN_SUPPLY,
  formatTokenAmount,
  formatUsd,
} from '@dcf/utils';
import {
  fetchBuilderRewardsLeaderboard,
  fetchBuilderRewardsMe,
  type BuilderRewardsEntry,
  type BuilderRewardsResponse,
} from '@/lib/api';
import { TwitterIdentityLink } from '@/components/account/twitter-identity-link';

function tierStyle(tier: BuilderRewardsEntry['tier']) {
  switch (tier) {
    case 'genesis':
      return 'bg-violet-500/25 text-violet-200 border-violet-400/40';
    case 'legend':
      return 'bg-amber-500/20 text-amber-200 border-amber-400/35';
    case 'platinum':
      return 'bg-sky-500/20 text-sky-200 border-sky-400/35';
    case 'gold':
      return 'bg-yellow-500/15 text-yellow-100 border-yellow-500/30';
    case 'silver':
      return 'bg-zinc-400/15 text-zinc-200 border-zinc-500/30';
    default:
      return 'bg-orange-950/40 text-orange-200 border-orange-500/25';
  }
}

function statusBadge(status: BuilderRewardsEntry['status']) {
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

function PoolEstimateDisclaimer({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`rounded-lg border border-zinc-700/80 bg-zinc-950/60 ${compact ? 'p-2.5 text-[10px]' : 'p-3 text-[11px]'} leading-relaxed text-zinc-500`}
    >
      <p className="font-semibold text-zinc-400">Illustrative pool math — not a guarantee</p>
      <p className="mt-1">
        Dollar and token figures below use a <strong className="text-zinc-300">hypothetical example only</strong>:
        {formatUsd(LAUNCH_FDV_USD, 0)} fully diluted valuation at launch ($1 per token on{' '}
        {TOKEN_SUPPLY.toLocaleString()} total supply), with {AIRDROP_SUPPLY_PERCENT}% of supply reserved for the
        community pool ({formatTokenAmount(AIRDROP_TOKEN_POOL)} tokens ≈ {formatUsd(AIRDROP_POOL_USD, 0)} at that
        example price). Real launch market cap, token price, pool size, and your allocation can differ and may
        change before any snapshot or distribution.
      </p>
    </div>
  );
}

function RulesBlock() {
  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 to-cyan-950/20 p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">Proof of contribution</p>
        <h2 className="mt-2 text-2xl font-bold text-white">How Builder Rewards Works</h2>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Builder Rewards is not an airdrop farm page. It measures building, trading, scouting, community
          participation, and reputation — then shows an <strong className="text-zinc-200">illustrative</strong>{' '}
          share of the future {AIRDROP_SUPPLY_PERCENT}% community pool under example launch assumptions (see
          disclaimer below). <strong className="text-zinc-200">DDollar alone does not decide your rank.</strong>
        </p>
        <PoolEstimateDisclaimer />
        <ul className="mt-4 grid gap-2 text-xs text-zinc-400 sm:grid-cols-2">
          <li>• Points from founder updates, GitHub/build feed, milestones</li>
          <li>• Scout stakes, reviews, and fraud signals</li>
          <li>• Comments, discussion, helpful feedback</li>
          <li>• Paper trades with conviction — not size</li>
          <li>• Reputation from trusted participation</li>
          <li>• Final cut at snapshot — bots and sybil clusters excluded</li>
        </ul>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h3 className="text-sm font-semibold text-white">Builder Rewards Rules</h3>
        <p className="mt-2 text-xs leading-relaxed text-zinc-400">
          Builder Rewards measures contribution to the ecosystem. DDollar balance alone does not determine
          rewards. Final reward allocations may consider DDollar earned, reputation, builder activity, scout
          accuracy, community participation, trading participation, account authenticity, sybil detection, and
          long-term consistency. The system is designed to reward real users and real builders.
        </p>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-xl border border-cyan-500/25 bg-cyan-950/15 p-4">
          <h3 className="text-sm font-semibold text-cyan-100">Eligibility</h3>
          <ol className="mt-3 list-decimal space-y-2 pl-4 text-xs text-zinc-300">
            <li>
              <strong className="text-white">X Account Required</strong> — sign in with a linked X (Twitter)
              account.
            </li>
            <li>Stay active — inactive accounts lose Builder Score (not DDollar).</li>
            <li>Suspected bot accounts may be excluded at snapshot.</li>
            <li>Sybil clusters (shared timing/behavior) may be excluded.</li>
            <li>Final eligibility determined at distribution snapshot.</li>
          </ol>
        </section>
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h3 className="text-sm font-semibold text-white">Blue tick</h3>
          <ul className="mt-3 space-y-2 text-xs text-zinc-400">
            <li>
              <strong className="text-emerald-300">Not required.</strong> No penalty without Blue.
            </li>
            <li>
              <strong className="text-cyan-300">+5% Builder Score</strong> when X verification is confirmed.
            </li>
            <li>Historical Blue or org verification = soft trust boost when verifiable.</li>
          </ul>
          <h3 className="mt-4 text-sm font-semibold text-white">Inactivity</h3>
          <p className="mt-2 text-xs text-zinc-400">
            After 21 days inactive: <strong className="text-amber-200">1% weekly contribution decay</strong>{' '}
            on Builder Score — not DDollar destruction. Become active to restore.
          </p>
        </section>
      </div>

      <section className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4">
        <p className="text-xs leading-relaxed text-amber-100/90">
          <strong className="text-amber-200">Estimated share is not a guarantee.</strong> Percentages and token
          amounts are scenario math from today&apos;s leaderboard — not promised allocations. Builder Rewards are
          based on contribution; DDollar alone does not guarantee rewards. Final allocation may consider activity,
          reputation, builder and scout participation, sybil detection, and community contribution. Rules may
          evolve to protect the ecosystem.
        </p>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-semibold text-white">Future snapshot weights (guidance)</h3>
        <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
          {(
            [
              ['ddollar', 'DDollar earned'],
              ['builderActivity', 'Builder activity'],
              ['reputation', 'Reputation'],
              ['scoutAccuracy', 'Scout accuracy'],
              ['communityActivity', 'Community'],
            ] as const
          ).map(([k, label]) => (
            <span key={k} className="rounded-full border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-400">
              {label}: {(BUILDER_REWARDS_SNAPSHOT_WEIGHTS[k] * 100).toFixed(0)}%
            </span>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-semibold text-white">Tiers & streaks</h3>
        <p className="mt-2 text-xs text-zinc-500">
          Bronze → Silver → Gold → Platinum → Legend → Genesis. Earn streak badges for 7 / 30 / 90 / 180 day
          activity, build-in-public streaks, and scout accuracy over time.
        </p>
      </section>
    </div>
  );
}

export function BuilderRewardsPage() {
  const { data: session } = useSession();
  const [board, setBoard] = useState<BuilderRewardsResponse | null>(null);
  const [me, setMe] = useState<Awaited<ReturnType<typeof fetchBuilderRewardsMe>> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchBuilderRewardsLeaderboard(100)
      .then(setBoard)
      .catch((e: Error) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (!session?.accessToken) {
      setMe(null);
      return;
    }
    fetchBuilderRewardsMe(session.accessToken)
      .then(setMe)
      .catch(() => setMe(null));
  }, [session?.accessToken]);

  return (
    <div className="space-y-10">
      <RulesBlock />

      {session && me?.warning && (
        <div
          className={`rounded-xl border p-4 ${
            me.warning.level === 'critical'
              ? 'border-red-500/50 bg-red-950/30'
              : 'border-amber-500/40 bg-amber-950/25'
          }`}
        >
          <p className="text-sm font-semibold text-white">System notice</p>
          <p className="mt-1 text-xs text-zinc-300">{me.warning.message}</p>
        </div>
      )}

      {!session && (
        <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4 text-sm text-amber-100">
          <Link href="/login?callbackUrl=%2Fbuilder-rewards" className="font-semibold text-cyan-300 underline">
            Sign in with X (Twitter)
          </Link>{' '}
          to join Builder Rewards. Google-only accounts are not eligible.
        </div>
      )}

      {session && me?.needsTwitter && (
        <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4 text-sm text-amber-100">
          Connect X in{' '}
          <Link href="/account?tab=connected" className="text-cyan-300 underline">
            Connected Accounts
          </Link>{' '}
          to earn Builder Score.
        </div>
      )}

      {session && me && !me.needsTwitter && me.rank != null && (
        <div className="rounded-xl border border-violet-500/30 bg-violet-950/20 p-4">
          <p className="text-xs text-violet-200/90">Your position</p>
          <p className="mt-1 text-lg font-bold text-white">
            #{me.rank} · {me.tierLabel} · Score {me.builderScore.toLocaleString()} · Illustrative pool share{' '}
            {me.rewardSharePercent.toFixed(2)}% (~{formatTokenAmount(me.estimatedTokens)} tokens ·{' '}
            {formatUsd(me.estimatedUsd, 0)} at example FDV)
          </p>
          <PoolEstimateDisclaimer compact />
        </div>
      )}

      <section>
        <h3 className="text-lg font-bold text-white">Builder Rewards Leaderboard</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Ranked by Builder Score — contribution, reputation, activity, building, scouting, and trading.
        </p>

        {err && <p className="mt-3 text-sm text-red-300">{err}</p>}

        {board && (
          <>
            <p className="mt-3 text-xs text-zinc-500">
              {board.twitterConnectedCount} X-linked accounts · {board.totalListed} scanned
            </p>
            <div className="mt-4 overflow-x-auto rounded-xl border border-zinc-800">
              <table className="w-full min-w-[900px] text-left text-xs">
                <thead className="border-b border-zinc-800 bg-zinc-900/80 text-[10px] uppercase tracking-wider text-zinc-500">
                  <tr>
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">User</th>
                    <th className="px-3 py-2">Tier</th>
                    <th className="px-3 py-2">Builder Score</th>
                    <th className="px-3 py-2">DDollar</th>
                    <th className="px-3 py-2">Reputation</th>
                    <th className="px-3 py-2">Activity</th>
                    <th className="px-3 py-2">Scout</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Illustrative share*</th>
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
                      <td className="px-3 py-2">
                        <span className="font-medium text-white">{row.displayName}</span>
                        {row.twitterHandle && (
                          <div className="mt-0.5">
                            <TwitterIdentityLink handle={row.twitterHandle} showLabel={false} />
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-semibold ${tierStyle(row.tier)}`}
                        >
                          {row.tierLabel.replace(' Builder', '')}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-semibold text-white">
                        {row.builderScore.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-zinc-400">
                        {row.ddollarBalanceUsd != null
                          ? `$${Math.round(row.ddollarBalanceUsd).toLocaleString()}`
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-zinc-400">{row.reputationPoints.toLocaleString()}</td>
                      <td className="px-3 py-2 text-zinc-400">{row.activityScore}</td>
                      <td className="px-3 py-2 text-zinc-400">{row.scoutScore}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${statusBadge(row.status)}`}
                        >
                          {row.status === 'decaying' ? 'Score decaying' : row.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-emerald-300/90">
                        {row.rewardSharePercent.toFixed(2)}%
                        <span className="block text-[10px] text-zinc-500">
                          ~{formatTokenAmount(row.estimatedTokens)} · {formatUsd(row.estimatedUsd, 0)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[10px] text-zinc-600">
              *Illustrative share of the example {AIRDROP_SUPPLY_PERCENT}% pool ({formatUsd(AIRDROP_POOL_USD, 0)} at{' '}
              {formatUsd(LAUNCH_FDV_USD, 0)} FDV). Not a guaranteed allocation. {board.rules.snapshotNote}
            </p>
          </>
        )}
      </section>
    </div>
  );
}

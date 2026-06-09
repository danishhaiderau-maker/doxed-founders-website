'use client';

import Link from 'next/link';
import { formatPercent } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import { ShareOnXButton } from '@/components/share-on-x-button';

export function AgentProfileHero({
  agent,
  slug,
  botConnected,
  executionPaused,
  following,
  hired,
  signedIn,
  onFollow,
  followBusy,
  shareText,
  builderHandle = '@bitbro4crypto',
}: {
  agent: TradingAgentSummary;
  slug: string;
  botConnected?: boolean;
  executionPaused?: boolean;
  following?: boolean;
  hired?: boolean;
  signedIn?: boolean;
  onFollow?: () => void;
  followBusy?: boolean;
  shareText?: string;
  builderHandle?: string;
}) {
  const isLive = botConnected && !executionPaused;
  const statusLabel = isLive ? 'LIVE (Admin showcase)' : executionPaused ? 'UPDATING' : 'OFFLINE';

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-900/90 via-zinc-950 to-emerald-950/20 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="max-w-2xl">
          <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-emerald-400">Trading agent</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-white sm:text-4xl">{agent.name}</h1>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-bold uppercase ${
                isLive
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300'
                  : 'border-zinc-600 bg-zinc-800 text-zinc-400'
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${isLive ? 'animate-pulse bg-emerald-400' : 'bg-zinc-500'}`} />
              {statusLabel}
            </span>
            <span className="rounded-full border border-amber-500/40 bg-amber-950/40 px-3 py-1 text-[10px] font-bold uppercase text-amber-200">
              High risk · Beta
            </span>
          </div>

          <dl className="mt-6 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-zinc-500">Built by</dt>
              <dd className="font-medium text-violet-300">{builderHandle}</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Strategy</dt>
              <dd className="text-zinc-200">Low-risk BTC trend following</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Showcase exchange</dt>
              <dd className="text-zinc-200">Bitfinex (admin account)</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Current position</dt>
              <dd className="font-semibold text-amber-200">{agent.currentPosition ?? 'FLAT'}</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Win rate</dt>
              <dd className="font-bold text-white">{agent.winRatePct.toFixed(0)}%</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Followers</dt>
              <dd className="font-bold text-white">{agent.followerCount.toLocaleString()}</dd>
            </div>
          </dl>
        </div>

        <div className="text-right">
          <p className="text-xs uppercase tracking-widest text-zinc-500">Net return</p>
          <p className={`text-4xl font-bold ${agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {formatPercent(agent.netReturnPct)}
          </p>
          <p className="mt-1 text-xs text-zinc-500">{agent.tradeCount} trades · {agent.liveSinceDays}d live</p>
        </div>
      </div>

      <p className="mt-6 rounded-xl border border-violet-500/20 bg-violet-950/20 px-4 py-3 text-sm text-violet-100/90">
        <strong className="text-violet-200">Observe live</strong> — watch the admin showcase (trades, reasoning, performance).
        Nobody else trades on this account.{' '}
        <strong className="text-emerald-200">Hire agent</strong> — run with your own Bitfinex API keys (recommended, zero fees).
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        <span className="rounded-lg bg-zinc-800 px-4 py-2 text-sm font-semibold text-zinc-300">
          Observe live ✓
        </span>
        <Link
          href={signedIn ? `/agent-hub/${slug}/hire` : `/login?callbackUrl=/agent-hub/${slug}/hire`}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
        >
          Hire agent · your keys
        </Link>
        {onFollow && (
          <button
            type="button"
            onClick={onFollow}
            disabled={followBusy}
            className="rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 hover:border-violet-500/50 disabled:opacity-50"
          >
            {following ? 'Following' : 'Follow'}
          </button>
        )}
        {hired && (
          <Link
            href={`/agent-hub/${slug}/my-dashboard`}
            className="rounded-lg border border-emerald-600/50 px-4 py-2 text-sm text-emerald-300"
          >
            My private dashboard
          </Link>
        )}
        {shareText && <ShareOnXButton text={shareText} label="Share" />}
      </div>
    </section>
  );
}

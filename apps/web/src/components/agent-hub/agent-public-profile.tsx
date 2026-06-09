'use client';

import Link from 'next/link';
import { useState } from 'react';
import {
  BITFINEX_RECOMMEND_BANNER,
  formatPercent,
  formatUsd,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentProfileHero } from '@/components/agent-hub/agent-profile-hero';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentTrustLayer } from '@/components/agent-hub/agent-trust-layer';
import { AgentActivityFeed } from '@/components/agent-hub/live-mission-control';
import type {
  PublicAgentStatus,
  TradingAgentActivityEntry,
  TradingAgentSummary,
} from '@/lib/api';

const TABS = ['Overview', 'Performance', 'Trade Journey', 'Reasoning', 'Activity'] as const;
type Tab = (typeof TABS)[number];

function PerformanceGrid({ agent, dashboard }: { agent: TradingAgentSummary; dashboard: TradingAgentDashboardState }) {
  const stats = [
    { label: '30D return', value: formatPercent(agent.netReturnPct), accent: agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Win rate', value: `${agent.winRatePct.toFixed(0)}%` },
    { label: 'Trades', value: String(agent.tradeCount) },
    { label: 'Equity', value: formatUsd(agent.equityUsd, 0) },
    { label: 'Position', value: dashboard.currentPosition },
    { label: 'Regime', value: dashboard.regime },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {stats.map((s) => (
        <div key={s.label} className="rounded-xl border border-zinc-800 bg-black/25 px-4 py-3">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">{s.label}</p>
          <p className={`mt-1 text-lg font-bold ${s.accent ?? 'text-white'}`}>{s.value}</p>
        </div>
      ))}
    </div>
  );
}

function PublicReasoningPanel({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const t = dashboard.currentThinking;
  return (
    <section className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-violet-300">Latest reasoning</h2>
      <p className="mt-1 text-xs text-zinc-500">Trader-friendly summary — raw AI inputs are never shown publicly.</p>
      <div className="mt-5 space-y-4 text-sm">
        <div>
          <p className="text-zinc-500">Market read</p>
          <p className="font-medium text-white">{t.market}</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-zinc-500">Support / Resistance</p>
            <p className="font-mono text-emerald-300">{t.support.toLocaleString()}</p>
            <p className="font-mono text-red-300">{t.resistance.toLocaleString()}</p>
          </div>
          <div>
            <p className="text-zinc-500">Edge vs required</p>
            <p className="text-lg font-bold text-amber-200">
              {dashboard.currentEdge} / {dashboard.requiredEdge}
            </p>
          </div>
        </div>
        <div>
          <p className="text-zinc-500">Conclusion</p>
          <p className="leading-relaxed text-zinc-200">{dashboard.aiReasoning}</p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          <span className="rounded-full border border-emerald-500/40 bg-emerald-950/40 px-3 py-1 text-emerald-200">
            Bias: {dashboard.regime}
          </span>
          <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
            Confidence: {dashboard.aiWinProbability}%
          </span>
          <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
            Action: {dashboard.currentAction}
          </span>
        </div>
      </div>
    </section>
  );
}

function HireSidebar({
  slug,
  signedIn,
  hired,
  instanceStatus,
  instanceMode,
  defaultSettings,
  onCopyAllocate,
  onPauseInstance,
  onResumeInstance,
  instanceBusy,
  copyBusy,
}: {
  slug: string;
  signedIn: boolean;
  hired: boolean;
  instanceStatus?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  defaultSettings?: string | null;
  onCopyAllocate?: () => void;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  instanceBusy?: boolean;
  copyBusy?: boolean;
}) {
  const isCopyActive = hired && instanceMode === 'copy';
  const isLiveActive = hired && instanceMode === 'live';

  return (
    <aside className="space-y-4 lg:sticky lg:top-24">
      <div className="rounded-2xl border border-violet-500/30 bg-violet-950/20 p-5">
        <p className="text-xs font-bold uppercase tracking-wider text-violet-300">How copy trading works</p>
        <p className="mt-2 text-sm text-violet-100/90">
          Admin runs one BTC bot with DeepSeek AI. Every user mirrors those same signals — when admin AI opens a trade,
          your copy track (or live exchange) follows.
        </p>
        <p className="mt-2 text-xs text-zinc-500">No separate bot per user. One AI brain, many followers.</p>
      </div>

      {defaultSettings && (
        <div className="rounded-2xl border border-amber-500/25 bg-amber-950/15 p-5">
          <p className="text-xs font-bold uppercase text-amber-300">Admin default settings</p>
          <p className="mt-2 text-sm text-amber-100/90">{defaultSettings}</p>
        </div>
      )}

      <div className="rounded-2xl border border-emerald-500/35 bg-emerald-950/20 p-5">
        <p className="text-sm font-bold text-emerald-200">Test with DDollar · $500 copy track</p>
        <p className="mt-2 text-xs text-emerald-100/80">
          Allocate up to $500 notional in DDollar. Mirrors admin DeepSeek trades in real time. No exchange API. No AI key.
        </p>
        {isCopyActive && (
          <p className="mt-2 rounded-lg bg-emerald-900/40 px-3 py-2 text-xs text-emerald-200">
            ✓ Copy track active · {instanceStatus === 'PAUSED' ? 'Paused' : 'Following admin AI'}
          </p>
        )}
        {signedIn && onCopyAllocate && !isLiveActive && (
          <button
            type="button"
            disabled={copyBusy || isCopyActive}
            onClick={onCopyAllocate}
            className="mt-4 w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {copyBusy ? 'Allocating…' : isCopyActive ? 'Copy track active' : 'Start $500 DDollar copy track'}
          </button>
        )}
        {!signedIn && (
          <Link
            href={`/login?callbackUrl=/agent-hub/${slug}`}
            className="mt-4 block rounded-lg bg-emerald-600 py-2.5 text-center text-sm font-semibold hover:bg-emerald-500"
          >
            Sign in to copy track
          </Link>
        )}
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        <p className="text-sm font-bold text-white">Go live · real money</p>
        <p className="mt-2 text-xs text-zinc-400">
          Connect your exchange only (Bitfinex recommended). Admin DeepSeek AI still drives every trade — you do not
          need your own AI API key.
        </p>
        <p className="mt-2 text-xs text-zinc-500">{BITFINEX_RECOMMEND_BANNER}</p>
        {isLiveActive && (
          <p className="mt-2 rounded-lg bg-violet-900/30 px-3 py-2 text-xs text-violet-200">
            ✓ Live copy trading connected
          </p>
        )}
        <Link
          href={signedIn ? `/agent-hub/${slug}/hire` : `/login?callbackUrl=/agent-hub/${slug}/hire`}
          className="mt-4 block rounded-lg border border-violet-500/50 bg-violet-600 py-2.5 text-center text-sm font-semibold hover:bg-violet-500"
        >
          {isLiveActive ? 'Manage live setup' : 'Connect exchange for live trading'}
        </Link>
      </div>

      {hired && signedIn && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
          <p className="font-semibold text-white">Your {instanceMode === 'live' ? 'live' : 'copy'} instance</p>
          <p className="mt-1 text-xs text-zinc-500">
            Status:{' '}
            <span className={instanceStatus === 'PAUSED' ? 'text-amber-300' : 'text-emerald-300'}>
              {instanceStatus === 'PAUSED' ? 'Paused' : 'Active'}
            </span>
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {instanceStatus === 'PAUSED' ? (
              <button
                type="button"
                disabled={instanceBusy}
                onClick={onResumeInstance}
                className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold hover:bg-emerald-500 disabled:opacity-50"
              >
                Resume copying
              </button>
            ) : (
              <button
                type="button"
                disabled={instanceBusy}
                onClick={onPauseInstance}
                className="rounded-lg border border-amber-500/40 px-3 py-2 text-xs font-semibold text-amber-200 hover:bg-amber-950/30 disabled:opacity-50"
              >
                Pause copying
              </button>
            )}
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-red-500/35 bg-red-950/20 p-5">
        <p className="text-sm font-bold text-red-200">Experimental · high risk</p>
        <p className="mt-2 text-xs text-red-100/80">
          Beta phase. Past performance ≠ future results. DDollar demo max $500. Live tier uses real funds on your
          exchange.
        </p>
      </div>
    </aside>
  );
}

export function AgentPublicProfile({
  slug,
  agent,
  dashboard,
  activity,
  following,
  hired,
  signedIn,
  botConnected,
  executionPaused,
  publicStatus,
  shareText,
  defaultSettings,
  instanceStatus,
  instanceMode,
  onFollow,
  followBusy,
  onCopyAllocate,
  onPauseInstance,
  onResumeInstance,
  instanceBusy,
  copyBusy,
}: {
  slug: string;
  agent: TradingAgentSummary;
  dashboard: TradingAgentDashboardState;
  activity: TradingAgentActivityEntry[];
  following: boolean;
  hired: boolean;
  signedIn: boolean;
  botConnected?: boolean;
  executionPaused?: boolean;
  publicStatus: PublicAgentStatus;
  shareText?: string;
  defaultSettings?: string | null;
  instanceStatus?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  onFollow?: () => void;
  followBusy?: boolean;
  onCopyAllocate?: () => void;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  instanceBusy?: boolean;
  copyBusy?: boolean;
}) {
  const [tab, setTab] = useState<Tab>('Overview');

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_300px]">
      <div className="min-w-0 space-y-6">
        <AgentProfileHero
          agent={agent}
          slug={slug}
          botConnected={botConnected}
          executionPaused={executionPaused}
          following={following}
          hired={hired}
          instanceMode={instanceMode}
          signedIn={signedIn}
          onFollow={onFollow}
          followBusy={followBusy}
          shareText={shareText}
          onCopyAllocate={onCopyAllocate}
          copyBusy={copyBusy}
        />

        <div className="flex flex-wrap gap-1 border-b border-zinc-800 pb-1">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`rounded-t-lg px-3 py-2 text-xs font-medium sm:text-sm ${
                tab === t ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {t}
              {t === 'Activity' && activity.length > 0 ? ` (${activity.length})` : ''}
            </button>
          ))}
        </div>

        {tab === 'Overview' && (
          <>
            <PerformanceGrid agent={agent} dashboard={dashboard} />
            <AgentTrustLayer />
            <AgentTradeJourney activity={activity} />
          </>
        )}
        {tab === 'Performance' && <PerformanceGrid agent={agent} dashboard={dashboard} />}
        {tab === 'Trade Journey' && <AgentTradeJourney activity={activity} />}
        {tab === 'Reasoning' && <PublicReasoningPanel dashboard={dashboard} />}
        {tab === 'Activity' && <AgentActivityFeed items={activity} />}

        {publicStatus === 'offline' && !botConnected && (
          <p className="text-sm text-amber-200/90">Showcase offline — stats may be cached. Admin runtime reconnecting.</p>
        )}
      </div>

      <HireSidebar
        slug={slug}
        signedIn={signedIn}
        hired={hired}
        instanceStatus={instanceStatus}
        instanceMode={instanceMode}
        defaultSettings={defaultSettings}
        onCopyAllocate={onCopyAllocate}
        onPauseInstance={onPauseInstance}
        onResumeInstance={onResumeInstance}
        instanceBusy={instanceBusy}
        copyBusy={copyBusy}
      />
    </div>
  );
}

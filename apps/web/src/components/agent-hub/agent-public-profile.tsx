'use client';

import Link from 'next/link';
import { useState } from 'react';
import {
  AGENT_BETA_RISK_COPY,
  BITFINEX_RECOMMEND_BANNER,
  EXCHANGE_API_GUIDES,
  formatPercent,
  formatUsd,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentMarketplaceStats } from '@/components/agent-hub/agent-marketplace-stats';
import { AgentAdminShowcaseControl } from '@/components/agent-hub/agent-admin-showcase-control';
import { AgentPerformanceChart } from '@/components/agent-hub/agent-performance-chart';
import { AgentTradeJourney } from '@/components/agent-hub/agent-trade-journey';
import { AgentActivityFeed } from '@/components/agent-hub/live-mission-control';
import { ExchangeApiGuideDrawer } from '@/components/agent-hub/exchange-api-guide-drawer';
import type {
  PublicAgentStatus,
  TradingAgentActivityEntry,
  TradingAgentSummary,
} from '@/lib/api';

const TABS = ['Overview', 'Performance', 'Trade Journey', 'Reasoning', 'Activity', 'Followers'] as const;
type Tab = (typeof TABS)[number];

function MetricPill({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-4 py-3 text-center">
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-1 text-lg font-bold ${accent ?? 'text-white'}`}>{value}</p>
    </div>
  );
}

function PublicReasoningPanel({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const t = dashboard.currentThinking;
  return (
    <section className="rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/30 to-zinc-950/50 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-violet-300">Latest reasoning</h2>
      <p className="mt-4 text-sm leading-relaxed text-zinc-200">{dashboard.aiReasoning || t.market}</p>
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border border-emerald-500/40 bg-emerald-950/40 px-3 py-1 font-semibold text-emerald-200">
          Bias: {dashboard.regime}
        </span>
        <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
          Confidence: {dashboard.aiWinProbability}%
        </span>
        <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
          Timeframe: 4H
        </span>
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
  onCopyAllocate?: () => void;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  instanceBusy?: boolean;
  copyBusy?: boolean;
}) {
  const [riskOk, setRiskOk] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const guide = EXCHANGE_API_GUIDES.bitfinex;

  return (
    <aside className="space-y-4 xl:sticky xl:top-24">
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/15 p-5">
        <p className="text-xs font-bold uppercase text-emerald-400">Currently tested on Bitfinex</p>
        <ul className="mt-3 space-y-1.5 text-xs text-emerald-100/80">
          <li>✓ Zero trading fees (eligible)</li>
          <li>✓ High liquidity</li>
          <li>✓ Reliable API</li>
          <li>✓ Institutional grade security</li>
        </ul>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        <p className="font-semibold text-white">Hire this agent</p>
        <ol className="mt-3 space-y-2 text-xs text-zinc-400">
          <li className="flex gap-2"><span className="text-violet-400">1</span> Choose exchange (Bitfinex)</li>
          <li className="flex gap-2"><span className="text-zinc-600">2</span> Connect exchange API</li>
          <li className="flex gap-2"><span className="text-zinc-600">3</span> Admin DeepSeek copy (no AI key)</li>
          <li className="flex gap-2"><span className="text-zinc-600">4</span> Risk acknowledgement</li>
          <li className="flex gap-2"><span className="text-zinc-600">5</span> Activate agent</li>
        </ol>
        <Link
          href={signedIn ? `/agent-hub/${slug}/hire` : `/login?callbackUrl=/agent-hub/${slug}/hire`}
          className="mt-4 block rounded-lg bg-violet-600 py-2.5 text-center text-sm font-semibold hover:bg-violet-500"
        >
          Start setup
        </Link>
      </div>

      <div className="rounded-2xl border border-red-500/40 bg-red-950/25 p-5">
        <p className="text-sm font-bold text-red-200">{AGENT_BETA_RISK_COPY.title}</p>
        <ul className="mt-2 space-y-1 text-xs text-red-100/80">
          {AGENT_BETA_RISK_COPY.bullets.slice(0, 3).map((b) => (
            <li key={b}>• {b}</li>
          ))}
        </ul>
        <label className="mt-3 flex items-start gap-2 text-xs text-red-100/90">
          <input type="checkbox" checked={riskOk} onChange={(e) => setRiskOk(e.target.checked)} className="mt-0.5" />
          {AGENT_BETA_RISK_COPY.checkboxLabel}
        </label>
        {signedIn && onCopyAllocate && (
          <button
            type="button"
            disabled={!riskOk || copyBusy || (hired && instanceMode === 'copy')}
            onClick={onCopyAllocate}
            className="mt-3 w-full rounded-lg border border-emerald-500/50 bg-emerald-950/40 py-2 text-sm font-semibold text-emerald-200 disabled:opacity-40"
          >
            {copyBusy ? 'Allocating…' : 'Paper track $500 (DDollar)'}
          </button>
        )}
      </div>

      {hired && signedIn && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4 text-xs">
          <p className="font-semibold text-white">Your copy instance</p>
          <p className="mt-1 text-zinc-500">
            {instanceMode === 'live' ? 'Live' : 'DDollar'} ·{' '}
            {instanceStatus === 'PAUSED' ? 'Paused' : 'Active'}
          </p>
          <button
            type="button"
            disabled={instanceBusy}
            onClick={instanceStatus === 'PAUSED' ? onResumeInstance : onPauseInstance}
            className="mt-2 rounded-lg border border-zinc-600 px-3 py-1.5 text-zinc-300 hover:text-white disabled:opacity-50"
          >
            {instanceStatus === 'PAUSED' ? 'Resume copying' : 'Pause copying'}
          </button>
        </div>
      )}

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        <button type="button" onClick={() => setGuideOpen(true)} className="text-sm font-semibold text-violet-300 hover:text-violet-200">
          How to get Bitfinex API key →
        </button>
        <p className="mt-2 text-xs text-zinc-500">{BITFINEX_RECOMMEND_BANNER}</p>
        {guide && (
          <ul className="mt-3 space-y-0.5 text-[10px] text-zinc-500">
            {guide.requiredPermissions.map((p) => (
              <li key={p}>✓ {p}</li>
            ))}
          </ul>
        )}
      </div>

      <ExchangeApiGuideDrawer provider="bitfinex" open={guideOpen} onClose={() => setGuideOpen(false)} />
    </aside>
  );
}

export function AgentPublicProfile({
  slug,
  agent,
  dashboard,
  activity,
  allAgents,
  following,
  hired,
  signedIn,
  isAdmin,
  adminToken,
  botConnected,
  executionPaused,
  publicStatus,
  instanceStatus,
  instanceMode,
  onFollow,
  followBusy,
  onCopyAllocate,
  onPauseInstance,
  onResumeInstance,
  onAdminRefresh,
  instanceBusy,
  copyBusy,
}: {
  slug: string;
  agent: TradingAgentSummary;
  dashboard: TradingAgentDashboardState;
  activity: TradingAgentActivityEntry[];
  allAgents?: TradingAgentSummary[];
  following: boolean;
  hired: boolean;
  signedIn: boolean;
  isAdmin?: boolean;
  adminToken?: string;
  botConnected?: boolean;
  executionPaused?: boolean;
  publicStatus: PublicAgentStatus;
  instanceStatus?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  onFollow?: () => void;
  followBusy?: boolean;
  onCopyAllocate?: () => void;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  onAdminRefresh?: () => void;
  instanceBusy?: boolean;
  copyBusy?: boolean;
}) {
  const [tab, setTab] = useState<Tab>('Overview');
  const isLive = botConnected && !executionPaused && publicStatus === 'online';

  const others = (allAgents ?? []).filter((a) => a.slug !== slug && a.status !== 'PAUSED').slice(0, 4);

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <AgentMarketplaceStats agents={allAgents ?? [agent]} builderCount={14} />

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_280px]">
        <div className="min-w-0 space-y-6">
          {/* Hero card */}
          <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-900/90 via-zinc-950 to-violet-950/20 p-6 sm:p-8">
            <div className="flex flex-wrap gap-6">
              <div className="flex min-w-0 flex-1 gap-4">
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-amber-500/20 text-2xl">
                  ₿
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-2xl font-bold sm:text-3xl">{agent.name}</h1>
                    <span className="text-blue-400">✓</span>
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
                        isLive ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40' : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {isLive ? 'Live' : publicStatus === 'updating' ? 'Updating' : 'Offline'}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-zinc-400">Low-Risk BTC Trend Following Strategy</p>
                  <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
                    <div><dt className="text-zinc-600">Built by</dt><dd className="text-violet-300">@bitbro4crypto · Verified</dd></div>
                    <div><dt className="text-zinc-600">Strategy</dt><dd>Trend following</dd></div>
                    <div><dt className="text-zinc-600">Timeframe</dt><dd>Swing · 4H / 1D</dd></div>
                    <div><dt className="text-zinc-600">AI</dt><dd>Admin DeepSeek (copy all users)</dd></div>
                  </dl>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-center sm:grid-cols-3 lg:w-64">
                <MetricPill label="Win rate" value={`${agent.winRatePct.toFixed(0)}%`} />
                <MetricPill label="30D return" value={formatPercent(agent.netReturnPct)} accent={agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                <MetricPill label="Trades" value={String(agent.tradeCount)} />
                <MetricPill label="Equity" value={formatUsd(agent.equityUsd, 0)} />
                <MetricPill label="Followers" value={agent.followerCount.toLocaleString()} />
                <MetricPill label="Position" value={agent.currentPosition ?? dashboard.currentPosition} accent="text-amber-300" />
              </div>
            </div>

            {isAdmin && adminToken && (
              <div className="mt-6">
                <AgentAdminShowcaseControl
                  token={adminToken}
                  executionPaused={executionPaused}
                  botConnected={botConnected}
                  onUpdated={onAdminRefresh}
                />
              </div>
            )}

            <div className="mt-6 flex flex-wrap gap-2">
              <span className="rounded-xl border border-zinc-600 bg-zinc-900/60 px-5 py-2.5 text-sm font-semibold text-zinc-200">
                Observe live ✓
              </span>
              <button
                type="button"
                disabled={copyBusy || !signedIn || (hired && instanceMode === 'copy')}
                onClick={onCopyAllocate}
                className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {copyBusy ? 'Starting…' : 'Paper trade · $500 DDollar'}
              </button>
              <Link
                href={signedIn ? `/agent-hub/${slug}/hire` : `/login?callbackUrl=/agent-hub/${slug}/hire`}
                className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
              >
                Hire agent · go live
              </Link>
              {onFollow && (
                <button
                  type="button"
                  onClick={onFollow}
                  disabled={followBusy}
                  className="rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-300 hover:border-violet-500/50 disabled:opacity-50"
                >
                  {following ? 'Following' : 'Follow'}
                </button>
              )}
            </div>
          </section>

          {/* Tabs */}
          <div className="flex flex-wrap gap-1 border-b border-zinc-800">
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`rounded-t-lg px-4 py-2.5 text-sm font-medium ${
                  tab === t ? 'border-b-2 border-violet-500 bg-zinc-900/50 text-white' : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {t}
                {t === 'Followers' ? ` (${(agent.followerCount / 1000).toFixed(1)}K)` : ''}
                {t === 'Activity' && activity.length ? ` (${activity.length})` : ''}
              </button>
            ))}
          </div>

          {tab === 'Overview' && (
            <div className="space-y-6">
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <MetricPill label="30D return" value={formatPercent(agent.netReturnPct)} accent="text-emerald-400" />
                <MetricPill label="Win rate" value={`${agent.winRatePct.toFixed(0)}%`} />
                <MetricPill label="Max drawdown" value="6.2%" />
                <MetricPill label="Total trades" value={String(agent.tradeCount)} />
                <MetricPill label="Sharpe" value="1.42" />
                <MetricPill label="Profit factor" value="2.31" />
              </div>
              <AgentTradeJourney activity={activity} layout="horizontal" />
              <PublicReasoningPanel dashboard={dashboard} />
              <div className="grid gap-6 lg:grid-cols-2">
                <AgentActivityFeed items={activity.slice(0, 8)} />
                <AgentPerformanceChart agentReturnPct={agent.netReturnPct} label={agent.name} />
              </div>
            </div>
          )}
          {tab === 'Performance' && (
            <AgentPerformanceChart agentReturnPct={agent.netReturnPct} label={agent.name} />
          )}
          {tab === 'Trade Journey' && <AgentTradeJourney activity={activity} />}
          {tab === 'Reasoning' && <PublicReasoningPanel dashboard={dashboard} />}
          {tab === 'Activity' && <AgentActivityFeed items={activity} />}
          {tab === 'Followers' && (
            <p className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-sm text-zinc-400">
              {agent.followerCount.toLocaleString()} founders follow this agent for trade alerts and bias updates.
            </p>
          )}

          {others.length > 0 && (
            <section>
              <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Top performing agents</h2>
              <div className="mt-4 flex gap-3 overflow-x-auto pb-2">
                {others.map((a) => (
                  <Link
                    key={a.id}
                    href={`/agent-hub/${a.slug}`}
                    className="min-w-[200px] shrink-0 rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 hover:border-violet-500/40"
                  >
                    <p className="font-semibold text-white">{a.name}</p>
                    <p className={`mt-1 text-sm font-bold ${a.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {formatPercent(a.netReturnPct)}
                    </p>
                    <p className="text-xs text-zinc-500">{a.winRatePct.toFixed(0)}% win · {a.tradeCount} trades</p>
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>

        <HireSidebar
          slug={slug}
          signedIn={signedIn}
          hired={hired}
          instanceStatus={instanceStatus}
          instanceMode={instanceMode}
          onCopyAllocate={onCopyAllocate}
          onPauseInstance={onPauseInstance}
          onResumeInstance={onResumeInstance}
          instanceBusy={instanceBusy}
          copyBusy={copyBusy}
        />
      </div>
    </div>
  );
}

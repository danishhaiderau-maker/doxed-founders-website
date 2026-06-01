'use client';

import type { ReactNode } from 'react';
import { formatPercent, formatUsd, type TradingAgentDashboardState } from '@dcf/utils';
import { ShareOnXButton } from '@/components/share-on-x-button';
import {
  AgentPublicStatusBanner,
  AgentPublicStatusInline,
} from '@/components/agent-hub/agent-public-status';
import type { PublicAgentStatus, TradingAgentActivityEntry, TradingAgentSummary } from '@/lib/api';
import { AgentWarningBanner } from './trading-agent-card';

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-black/20 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${accent ?? 'text-zinc-100'}`}>{value}</p>
    </div>
  );
}

export function CurrentThinkingPanel({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const t = dashboard.currentThinking;
  return (
    <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 to-zinc-950/40 p-5">
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Current Thinking</p>
      <div className="mt-4 space-y-3 text-sm">
        <div>
          <p className="text-zinc-500">Current Market</p>
          <p className="font-medium text-white">{t.market}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <p className="text-zinc-500">Support</p>
            <p className="font-mono text-lg text-emerald-300">{t.support.toLocaleString()}</p>
          </div>
          <div>
            <p className="text-zinc-500">Resistance</p>
            <p className="font-mono text-lg text-red-300">{t.resistance.toLocaleString()}</p>
          </div>
          <div>
            <p className="text-zinc-500">Distance to resistance</p>
            <p className="text-zinc-200">{t.distanceToResistancePct.toFixed(2)}%</p>
          </div>
          <div>
            <p className="text-zinc-500">Distance to support</p>
            <p className="text-zinc-200">{t.distanceToSupportPct.toFixed(2)}%</p>
          </div>
        </div>
        <div>
          <p className="text-zinc-500">Conclusion</p>
          <p className="text-base font-medium text-amber-100">{t.conclusion}</p>
        </div>
      </div>
    </section>
  );
}

export function TransparencyPanel({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const t = dashboard.transparency;
  return (
    <section className="rounded-2xl border border-amber-500/25 bg-amber-950/10 p-5">
      <h2 className="text-sm font-semibold text-amber-100">Why Didn&apos;t The Agent Trade?</h2>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current edge</dt>
          <dd className="text-2xl font-bold text-red-300">{t.currentEdge}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Required edge</dt>
          <dd className="text-2xl font-bold text-emerald-300">{t.requiredEdge}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current state</dt>
          <dd className="font-medium text-zinc-200">{t.currentState}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Reason</dt>
          <dd className="font-medium text-zinc-200">{t.reason}</dd>
        </div>
      </dl>
    </section>
  );
}

export function AgentActivityFeed({ items }: { items: TradingAgentActivityEntry[] }) {
  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Agent Activity Feed</h2>
      {items.length === 0 ? (
        <p className="mt-4 text-sm text-zinc-500">No activity yet.</p>
      ) : (
        <ul className="mt-4 space-y-0">
          {items.map((item, idx) => (
            <li key={item.id} className="border-t border-zinc-800/80 py-4 first:border-t-0 first:pt-0">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] text-zinc-500">
                    {new Date(item.createdAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-white">{item.title}</p>
                  {item.outcome && <p className="text-xs text-zinc-400">Outcome: {item.outcome}</p>}
                  {item.reason && <p className="mt-1 text-sm text-zinc-300">Reason: {item.reason}</p>}
                  {item.profitPct != null && (
                    <p className={`mt-1 text-sm font-medium ${item.profitPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      Profit: {formatPercent(item.profitPct)}
                    </p>
                  )}
                  {item.edgeScore != null && item.edgeRequired != null && (
                    <p className="mt-1 text-xs text-zinc-500">
                      Edge {item.edgeScore}/{item.edgeRequired}
                      {item.marketRegime ? ` · ${item.marketRegime}` : ''}
                    </p>
                  )}
                </div>
                {item.shareText && (
                  <ShareOnXButton text={item.shareText} label="Share to X" className="shrink-0" />
                )}
              </div>
              {idx < items.length - 1 && <div className="mt-4 border-b border-zinc-800/50" />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function BotConnectionBanner({
  botConnected,
  botSource,
  executionPaused,
  publicStatus,
  publicLabel,
  isAdmin,
  adminDetails,
}: {
  botConnected?: boolean;
  botSource?: 'LIVE' | 'FALLBACK';
  strategyMode?: string | null;
  executionPaused?: boolean;
  executionReason?: string | null;
  publicStatus?: PublicAgentStatus;
  publicLabel?: string;
  isAdmin?: boolean;
  adminDetails?: ReactNode;
}) {
  const status: PublicAgentStatus =
    publicStatus ??
    (botConnected && botSource === 'LIVE'
      ? executionPaused
        ? 'updating'
        : 'online'
      : 'offline');
  const label =
    publicLabel ??
    (status === 'online' ? 'Agent online' : status === 'updating' ? 'Agent updating' : 'Agent offline');

  return (
    <div className="space-y-3">
      <AgentPublicStatusBanner status={status} label={label} />
      {isAdmin && adminDetails && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-950/10 px-4 py-3 text-xs text-amber-100/90">
          {adminDetails}
        </div>
      )}
    </div>
  );
}

export { AgentPublicStatusInline };

export function LiveMissionControl({
  agent,
  dashboard,
  activity,
  botConnected,
  botSource,
  executionPaused,
  isAdmin,
  adminDetails,
}: {
  agent: TradingAgentSummary;
  dashboard: TradingAgentDashboardState;
  activity: TradingAgentActivityEntry[];
  botConnected?: boolean;
  botSource?: 'LIVE' | 'FALLBACK';
  strategyMode?: string | null;
  executionPaused?: boolean;
  executionReason?: string | null;
  publicStatus?: PublicAgentStatus;
  publicLabel?: string;
  isAdmin?: boolean;
  adminDetails?: React.ReactNode;
}) {
  const d = dashboard;
  return (
    <div className="space-y-6">
      <AgentWarningBanner />
      {isAdmin && adminDetails && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-950/10 px-4 py-3 text-xs text-amber-100/90">
          {adminDetails}
        </div>
      )}

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Live Mission Control</p>
          <h1 className="mt-1 text-2xl font-bold text-white">{agent.name}</h1>
          <p className="text-sm text-zinc-500">
            {agent.assetSymbol} · {agent.status} ·{' '}
            <AgentPublicStatusInline
              status={botConnected && botSource === 'LIVE' ? (executionPaused ? 'updating' : 'online') : 'offline'}
              label={
                botConnected && botSource === 'LIVE'
                  ? executionPaused
                    ? 'Agent updating'
                    : 'Agent online'
                  : 'Agent offline'
              }
            />
          </p>
        </div>
        <div className="text-right text-sm">
          <p className="text-zinc-500">Net return</p>
          <p className={`text-xl font-bold ${agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {formatPercent(agent.netReturnPct)}
          </p>
        </div>
      </div>

      <CurrentThinkingPanel dashboard={d} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Current price" value={formatUsd(d.currentPrice, 0)} />
        <Metric label="Regime" value={d.regime} accent="text-violet-300" />
        <Metric label="AI decision" value={d.aiDecision} accent="text-amber-200" />
        <Metric label="Win probability" value={`${d.aiWinProbability}%`} />
        <Metric label="Position" value={d.currentPosition} />
        <Metric label="Action" value={d.currentAction} accent="text-amber-200" />
        <Metric label="Daily PnL" value={formatPercent(d.pnl.daily)} accent={d.pnl.daily >= 0 ? 'text-emerald-400' : 'text-red-400'} />
        <Metric label="Total PnL" value={formatPercent(d.pnl.total)} accent={d.pnl.total >= 0 ? 'text-emerald-400' : 'text-red-400'} />
      </div>

      <TransparencyPanel dashboard={d} />

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
          <h2 className="text-sm font-semibold text-zinc-300">Open Trades</h2>
          {d.openTrades.length === 0 ? (
            <p className="mt-3 text-sm text-zinc-500">No open positions.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {d.openTrades.map((t, i) => (
                <li key={i} className="rounded-lg bg-black/20 px-3 py-2 text-sm">
                  {t.side} · {formatUsd(t.sizeUsd, 0)} · {formatPercent(t.unrealizedPct)}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
          <h2 className="text-sm font-semibold text-zinc-300">Pending Orders</h2>
          {d.pendingOrders.length === 0 ? (
            <p className="mt-3 text-sm text-zinc-500">No pending orders.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {d.pendingOrders.map((o, i) => (
                <li key={i} className="rounded-lg bg-black/20 px-3 py-2 text-sm">
                  {o.side} @ {formatUsd(o.triggerPrice, 0)}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
        <h2 className="text-sm font-semibold text-zinc-300">Recent Trades</h2>
        <ul className="mt-3 divide-y divide-zinc-800/80">
          {d.recentTrades.map((t, i) => (
            <li key={i} className="flex flex-wrap justify-between gap-2 py-2 text-sm">
              <span className="text-zinc-300">
                {t.side} · {t.entryPrice.toLocaleString()} → {t.exitPrice.toLocaleString()}
              </span>
              <span className={t.profitPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                {formatPercent(t.profitPct)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
          <h2 className="text-sm font-semibold text-zinc-300">Market Structure</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">{d.marketStructure}</p>
        </section>
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
          <h2 className="text-sm font-semibold text-zinc-300">AI Reasoning</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">{d.aiReasoning}</p>
        </section>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Risk status" value={d.riskStatus} />
        <Metric label="Funding" value={d.fundingStatus} />
        <Metric label="Data source" value={d.dataSource} />
        <Metric label="WS health" value={d.wsHealth} accent="text-emerald-400" />
        <Metric label="Data quality" value={d.dataQuality} accent="text-emerald-400" />
        <Metric label="Support" value={d.support.toLocaleString()} />
        <Metric label="Resistance" value={d.resistance.toLocaleString()} />
        <Metric label="Required edge" value={String(d.requiredEdge)} />
      </div>

      <AgentActivityFeed items={activity} />
    </div>
  );
}

'use client';

import { formatUsd } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

export function AgentMarketplaceStats({
  agents,
  builderCount = 1,
}: {
  agents: TradingAgentSummary[];
  builderCount?: number;
}) {
  const live = agents.filter((a) => a.status !== 'PAUSED');
  const activeCount = live.length;
  const avgWin =
    live.length > 0
      ? live.reduce((s, a) => s + a.winRatePct, 0) / live.length
      : 0;
  const totalVolume = live.reduce((s, a) => s + a.equityUsd, 0);

  const stats = [
    { label: 'Active agents', value: String(activeCount) },
    { label: 'Simulated volume', value: formatUsd(totalVolume, 0) },
    { label: 'Avg win rate', value: `${avgWin.toFixed(0)}%` },
    { label: 'Live builders', value: String(builderCount) },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {stats.map((s) => (
        <div
          key={s.label}
          className="rounded-2xl border border-zinc-800/80 bg-gradient-to-br from-zinc-900/60 to-black/40 px-4 py-4"
        >
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-zinc-500">{s.label}</p>
          <p className="mt-1 text-2xl font-bold text-white">{s.value}</p>
        </div>
      ))}
    </div>
  );
}

export function AgentMarketplaceTabs({
  active,
  onChange,
}: {
  active: string;
  onChange: (tab: string) => void;
}) {
  const tabs = [
    { id: '', label: 'Discover' },
    { id: 'TRADING', label: 'Trading' },
    { id: 'RESEARCH', label: 'Research' },
    { id: 'FOUNDER', label: 'Content' },
    { id: 'SCOUT', label: 'Scout' },
  ];
  return (
    <div className="flex flex-wrap gap-2">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
            active === t.id
              ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-900/40'
              : 'border border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function AgentRankingTabs({
  active,
  onChange,
}: {
  active: 'pnl' | 'followed' | 'builders';
  onChange: (tab: 'pnl' | 'followed' | 'builders') => void;
}) {
  const tabs = [
    { id: 'pnl' as const, label: 'Top traders' },
    { id: 'followed' as const, label: 'Most followed' },
    { id: 'builders' as const, label: 'Best builders' },
  ];
  return (
    <div className="flex gap-1 rounded-xl border border-zinc-800 p-1">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
            active === t.id ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

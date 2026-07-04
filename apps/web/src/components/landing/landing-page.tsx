'use client';

import { useEffect, useState } from 'react';
import {
  fetchPlatformStats,
  fetchTradingAgentLeaderboard,
  fetchLeaderboard,
  fetchBuilderRewardsLeaderboard,
  fetchAnalyzerSessionSummary,
  PlatformStats,
} from '@/lib/api';
import type { LandingHighlights } from '@/components/landing/landing-live-highlights';
import { LandingHeader, LandingSinglePage } from '@/components/landing/landing-mockup-sections';
import type { TradingAgentSummary } from '@/lib/api';

// Override the conservative-btc agent's stats with the analyzer / full-session
// summary so the landing-page highlights match the full-session panel on the profile
// page. Other agents keep their DB-sourced leaderboard numbers.
//
// Only apply when the summary has real session data. Reject the fabricated
// $500 equity / 0 trades / $0 PnL envelope that used to appear when the analyzer
// was intermittent and the API fell back to slim relay-state defaults.
function isRealSessionSummary(summary: {
  ok: boolean;
  total_pnl_pct?: number;
  total_pnl_usd?: number;
  trade_count?: number;
  win_rate?: number;
  current_balance?: number;
} | null): boolean {
  if (!summary || summary.ok !== true) return false;
  const trades = summary.trade_count;
  const balance = summary.current_balance;
  const pnl = summary.total_pnl_usd;
  if (typeof trades === 'number' && trades > 0) return true;
  if (typeof balance === 'number' && Number.isFinite(balance) && balance !== 500) return true;
  if (typeof pnl === 'number' && Number.isFinite(pnl) && pnl !== 0) return true;
  return false;
}

function withAnalyzerOverride(
  agent: TradingAgentSummary,
  summary: { ok: boolean; total_pnl_pct?: number; total_pnl_usd?: number; trade_count?: number; win_rate?: number; current_balance?: number } | null,
): TradingAgentSummary {
  if (agent.slug !== 'conservative-btc' || !isRealSessionSummary(summary)) return agent;
  return {
    ...agent,
    netReturnPct: summary!.total_pnl_pct ?? agent.netReturnPct,
    sessionPnlUsd: summary!.total_pnl_usd ?? agent.sessionPnlUsd,
    tradeCount: summary!.trade_count ?? agent.tradeCount,
    winRatePct: summary!.win_rate ?? agent.winRatePct,
    equityUsd: summary!.current_balance ?? agent.equityUsd,
    balanceUsd: summary!.current_balance ?? agent.balanceUsd,
  };
}

export function LandingPage() {
  const [platformStats, setPlatformStats] = useState<PlatformStats | null>(null);
  const [highlights, setHighlights] = useState<LandingHighlights | null>(null);

  useEffect(() => {
    Promise.all([
      fetchPlatformStats().catch(() => null),
      fetchTradingAgentLeaderboard().catch(() => [] as Awaited<ReturnType<typeof fetchTradingAgentLeaderboard>>),
      fetchLeaderboard().catch(() => [] as Awaited<ReturnType<typeof fetchLeaderboard>>),
      fetchBuilderRewardsLeaderboard(5).catch(() => null),
      fetchAnalyzerSessionSummary('conservative-btc').catch(() => null),
    ]).then(([stats, agents, traders, builders, analyzerSummary]) => {
      setPlatformStats(stats);
      const overriddenAgents = agents.map((a) => withAnalyzerOverride(a, analyzerSummary));
      setHighlights({
        agents: overriddenAgents,
        topAgent: overriddenAgents[0] ?? null,
        traders,
        topTrader: traders[0] ?? null,
        topBuilder: builders?.entries[0] ?? null,
      });
    });
  }, []);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <LandingHeader />
      <LandingSinglePage stats={platformStats} highlights={highlights} />
    </main>
  );
}

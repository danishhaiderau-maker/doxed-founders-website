'use client';

import { useEffect, useState } from 'react';
import {
  fetchPlatformStats,
  fetchTradingAgentLeaderboard,
  fetchLeaderboard,
  fetchBuilderRewardsLeaderboard,
  PlatformStats,
} from '@/lib/api';
import type { LandingHighlights } from '@/components/landing/landing-live-highlights';
import { LandingHeader, LandingSinglePage } from '@/components/landing/landing-mockup-sections';

export function LandingPage() {
  const [platformStats, setPlatformStats] = useState<PlatformStats | null>(null);
  const [highlights, setHighlights] = useState<LandingHighlights | null>(null);

  useEffect(() => {
    Promise.all([
      fetchPlatformStats().catch(() => null),
      fetchTradingAgentLeaderboard().catch(() => [] as Awaited<ReturnType<typeof fetchTradingAgentLeaderboard>>),
      fetchLeaderboard().catch(() => [] as Awaited<ReturnType<typeof fetchLeaderboard>>),
      fetchBuilderRewardsLeaderboard(5).catch(() => null),
    ]).then(([stats, agents, traders, builders]) => {
      setPlatformStats(stats);
      setHighlights({
        agents,
        topAgent: agents[0] ?? null,
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

'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { PushNotificationPrompt } from '@/components/push-notification-prompt';
import { FeedHubCategoryTabs } from '@/components/feed/feed-hub-category-tabs';
import { FeedHubPulseStrip } from '@/components/feed/feed-hub-pulse-strip';
import { FeedHubStream } from '@/components/feed/feed-hub-stream';
import { FeedTerminalTabs } from '@/components/feed/feed-terminal-tabs';
import { FeedProjectBubbleStrip } from '@/components/feed/feed-project-bubble-strip';
import { FeedDdFlowBar } from '@/components/feed/feed-dd-flow-bar';
import { FeedTerminalSidebar } from '@/components/feed/feed-terminal-sidebar';
import { FeedMoneySections } from '@/components/feed/feed-money-sections';
import { FeedLiveTape } from '@/components/feed/feed-live-tape';
import {
  fetchDiscoverUniverse,
  fetchFeedHub,
  type DiscoverUniverseResponse,
  type FeedHubResponse,
  type FeedTerminalCard,
  type FeedTerminalTab,
  type UnifiedFeedCategory,
} from '@/lib/api';
import { markFeedSeen } from '@/hooks/use-feed-new-count';

const VALID_TABS: FeedTerminalTab[] = [
  'all',
  'trades',
  'conviction',
  'movers',
  'regret',
  'listings',
];

const VALID_CATEGORIES: UnifiedFeedCategory[] = [
  'all',
  'trading',
  'founder',
  'market',
  'community',
];

function showTerminalTabs(category: UnifiedFeedCategory) {
  return category === 'all' || category === 'trading';
}

export default function FeedPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508]" />}>
      <FeedHubPage />
    </Suspense>
  );
}

function FeedHubPage() {
  const searchParams = useSearchParams();
  const tabParam = searchParams.get('tab') as FeedTerminalTab | null;
  const categoryParam = searchParams.get('category') as UnifiedFeedCategory | null;
  const projectParam = searchParams.get('project');

  const [category, setCategory] = useState<UnifiedFeedCategory>(
    categoryParam && VALID_CATEGORIES.includes(categoryParam) ? categoryParam : 'all',
  );
  const [tab, setTab] = useState<FeedTerminalTab>(
    tabParam && VALID_TABS.includes(tabParam) ? tabParam : 'all',
  );
  const [projectSlug, setProjectSlug] = useState<string | null>(projectParam);
  const [hub, setHub] = useState<FeedHubResponse | null>(null);
  const [universe, setUniverse] = useState<DiscoverUniverseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tapeCards, setTapeCards] = useState<FeedTerminalCard[]>([]);

  const terminalCardsById = useMemo(() => {
    const map = new Map<string, FeedTerminalCard>();
    for (const card of hub?.terminal?.cards ?? []) {
      map.set(card.id, card);
    }
    return map;
  }, [hub?.terminal?.cards]);

  const load = useCallback(async () => {
    try {
      const [hubRes, uni, tapeHub] = await Promise.all([
        fetchFeedHub(category, tab, projectSlug ?? undefined),
        fetchDiscoverUniverse({ timeframe: '24h', bubbleMode: 'feed' }),
        fetchFeedHub('all', 'trades', projectSlug ?? undefined, 80),
      ]);
      setHub(hubRes);
      setUniverse(uni);
      setTapeCards(tapeHub.sections?.tape ?? hubRes.sections?.tape ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load feed');
    } finally {
      setLoading(false);
    }
  }, [category, tab, projectSlug]);

  useEffect(() => {
    setLoading(true);
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    markFeedSeen();
  }, []);

  useEffect(() => {
    if (tabParam && VALID_TABS.includes(tabParam)) setTab(tabParam);
  }, [tabParam]);

  useEffect(() => {
    if (categoryParam && VALID_CATEGORIES.includes(categoryParam)) setCategory(categoryParam);
  }, [categoryParam]);

  useEffect(() => {
    setProjectSlug(projectParam);
  }, [projectParam]);

  function updateUrl(
    nextCategory: UnifiedFeedCategory,
    nextTab: FeedTerminalTab,
    nextProject: string | null,
  ) {
    const qs = new URLSearchParams();
    if (nextCategory !== 'all') qs.set('category', nextCategory);
    if (showTerminalTabs(nextCategory) && nextTab !== 'all') qs.set('tab', nextTab);
    if (nextProject) qs.set('project', nextProject);
    const q = qs.toString();
    window.history.replaceState(null, '', q ? `/feed?${q}` : '/feed');
  }

  function handleCategoryChange(next: UnifiedFeedCategory) {
    setCategory(next);
    updateUrl(next, tab, projectSlug);
  }

  function handleTabChange(next: FeedTerminalTab) {
    setTab(next);
    updateUrl(category, next, projectSlug);
  }

  function handleProjectSelect(slug: string | null) {
    setProjectSlug(slug);
    updateUrl(category, tab, slug);
  }

  const showTerminal = showTerminalTabs(category);

  return (
    <div className="min-h-screen bg-[#050508]">
      <PushNotificationPrompt />
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-bold tracking-tight">Money Feed</h1>
            <p className="text-xs text-zinc-500">
              Conviction · flow · listings · markets — builds & commits live on Discover & Founder OS
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[90rem] px-4 py-4 sm:px-6 lg:px-10">
        <div className="mb-5">
          <FeedLiveTape cards={tapeCards} variant="hero" loading={loading && tapeCards.length === 0} />
        </div>

        <FeedHubCategoryTabs active={category} onChange={handleCategoryChange} />

        {hub?.pulse && hub.pulse.length > 0 && (
          <div className="mt-4">
            <FeedHubPulseStrip pulse={hub.pulse} />
          </div>
        )}

        {showTerminal && (
          <div className="mt-5">
            <FeedTerminalTabs active={tab} onChange={handleTabChange} />
          </div>
        )}

        {universe && showTerminal && (
          <div className="mt-5 space-y-4">
            <p className="text-[10px] uppercase tracking-wider text-zinc-600">
              Activity bubbles · size = trading volume + watchlists + votes (not GitHub commits)
            </p>
            <FeedProjectBubbleStrip
              projects={universe.projects}
              selectedSlug={projectSlug}
              onSelect={handleProjectSelect}
            />
            <FeedDdFlowBar sidebar={universe.sidebar} />
          </div>
        )}

        {hub?.sections && category === 'all' && showTerminal && (
          <div className="mt-5">
            <FeedMoneySections sections={hub.sections} />
          </div>
        )}

        {error && (
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_280px]">
          <div className="space-y-3">
            {loading && !hub && (
              <div className="rounded-xl border border-zinc-800 p-12 text-center text-zinc-500">
                Loading platform feed…
              </div>
            )}
            {hub?.stream.length === 0 && !loading && (
              <div className="rounded-xl border border-dashed border-zinc-800 p-12 text-center text-zinc-500">
                No activity in this view yet.{' '}
                {category === 'trading' || category === 'all' ? (
                  <Link href="/paper-trading" className="text-violet-400 hover:underline">
                    Open Trading Alpha →
                  </Link>
                ) : (
                  <Link href="/founder-den" className="text-amber-300 hover:underline">
                    Founder Den →
                  </Link>
                )}
              </div>
            )}
            {hub && hub.stream.length > 0 && (
              <>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                  {category === 'founder' ? 'Founder milestones' : 'Live stream'}
                </h3>
                <FeedHubStream stream={hub.stream} terminalCardsById={terminalCardsById} />
              </>
            )}
          </div>

          {hub?.terminal && universe && showTerminal && (
            <FeedTerminalSidebar
              terminal={hub.terminal}
              trending={universe.sidebar.trending}
              scoutPending={hub.terminal.scoutPending}
            />
          )}
        </div>
      </main>
    </div>
  );
}

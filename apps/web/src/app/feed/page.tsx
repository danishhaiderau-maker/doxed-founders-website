'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { PushNotificationPrompt } from '@/components/push-notification-prompt';
import { FeedTerminalTabs } from '@/components/feed/feed-terminal-tabs';
import { FeedProjectBubbleStrip } from '@/components/feed/feed-project-bubble-strip';
import { FeedDdFlowBar } from '@/components/feed/feed-dd-flow-bar';
import { FeedConvictionCard } from '@/components/feed/feed-conviction-card';
import { FeedTerminalSidebar } from '@/components/feed/feed-terminal-sidebar';
import {
  fetchDiscoverUniverse,
  fetchFeedTerminal,
  type DiscoverUniverseResponse,
  type FeedTerminalResponse,
  type FeedTerminalTab,
} from '@/lib/api';

const VALID_TABS: FeedTerminalTab[] = [
  'all',
  'trades',
  'conviction',
  'movers',
  'regret',
  'activity',
];

export default function FeedPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508]" />}>
      <FeedTerminalPage />
    </Suspense>
  );
}

function FeedTerminalPage() {
  const searchParams = useSearchParams();
  const tabParam = searchParams.get('tab') as FeedTerminalTab | null;
  const projectParam = searchParams.get('project');

  const [tab, setTab] = useState<FeedTerminalTab>(
    tabParam && VALID_TABS.includes(tabParam) ? tabParam : 'all',
  );
  const [projectSlug, setProjectSlug] = useState<string | null>(projectParam);
  const [terminal, setTerminal] = useState<FeedTerminalResponse | null>(null);
  const [universe, setUniverse] = useState<DiscoverUniverseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [term, uni] = await Promise.all([
        fetchFeedTerminal(tab, projectSlug ?? undefined),
        fetchDiscoverUniverse({ timeframe: '24h' }),
      ]);
      setTerminal(term);
      setUniverse(uni);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load feed');
    } finally {
      setLoading(false);
    }
  }, [tab, projectSlug]);

  useEffect(() => {
    setLoading(true);
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    if (tabParam && VALID_TABS.includes(tabParam)) setTab(tabParam);
  }, [tabParam]);

  useEffect(() => {
    setProjectSlug(projectParam);
  }, [projectParam]);

  function updateUrl(nextTab: FeedTerminalTab, nextProject: string | null) {
    const qs = new URLSearchParams();
    if (nextTab !== 'all') qs.set('tab', nextTab);
    if (nextProject) qs.set('project', nextProject);
    const q = qs.toString();
    window.history.replaceState(null, '', q ? `/feed?${q}` : '/feed');
  }

  function handleTabChange(next: FeedTerminalTab) {
    setTab(next);
    updateUrl(next, projectSlug);
  }

  function handleProjectSelect(slug: string | null) {
    setProjectSlug(slug);
    updateUrl(tab, slug);
  }

  return (
    <div className="min-h-screen bg-[#050508]">
      <PushNotificationPrompt />
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-bold tracking-tight">Social Conviction Terminal</h1>
            <p className="text-xs text-zinc-500">
              Trades · conviction · regret · DDollar flow — not another chat room
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[90rem] px-4 py-6 sm:px-6 lg:px-10">
        <FeedTerminalTabs active={tab} onChange={handleTabChange} />

        {universe && (
          <div className="mt-5 space-y-4">
            <FeedProjectBubbleStrip
              projects={universe.projects}
              selectedSlug={projectSlug}
              onSelect={handleProjectSelect}
            />
            <FeedDdFlowBar sidebar={universe.sidebar} />
          </div>
        )}

        {error && (
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_280px]">
          <div className="space-y-3">
            {loading && !terminal && (
              <div className="rounded-xl border border-zinc-800 p-12 text-center text-zinc-500">
                Loading conviction feed…
              </div>
            )}
            {terminal?.cards.length === 0 && !loading && (
              <div className="rounded-xl border border-dashed border-zinc-800 p-12 text-center text-zinc-500">
                No {tab === 'all' ? '' : tab} activity yet.{' '}
                <Link href="/paper-trading" className="text-violet-400 hover:underline">
                  Open Trading Alpha →
                </Link>
              </div>
            )}
            {terminal?.cards.map((card) => (
              <FeedConvictionCard key={card.id} card={card} />
            ))}
          </div>

          {terminal && universe && (
            <FeedTerminalSidebar
              terminal={terminal}
              trending={universe.sidebar.trending}
              scoutPending={terminal.scoutPending}
            />
          )}
        </div>
      </main>
    </div>
  );
}

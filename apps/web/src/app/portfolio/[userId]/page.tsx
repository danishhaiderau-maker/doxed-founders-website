'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { CoinIntelligencePanel, type CoinIntelData } from '@/components/coin-intelligence-panel';
import { SharePortfolio } from '@/components/share-portfolio';
import { TraderProfileHeader } from '@/components/trader/profile-header';
import { TraderOpenPositionCard } from '@/components/trader/open-position-card';
import { TraderClosedTradeCard } from '@/components/trader/closed-trade-card';
import { TradingTimeline } from '@/components/trader/trading-timeline';
import { fetchAccountFollowing, fetchPublicPortfolio, PublicPortfolio } from '@/lib/api';

type PublicPosition = PublicPortfolio['positions'][number];

export default function PublicPortfolioPage() {
  const params = useParams<{ userId: string }>();
  const { data: session } = useSession();
  const userId = params.userId;
  const [portfolio, setPortfolio] = useState<PublicPortfolio | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [showOlder, setShowOlder] = useState(false);
  const [intelPosition, setIntelPosition] = useState<CoinIntelData | null>(null);
  const [following, setFollowing] = useState(false);

  const isSelf = session?.user?.id === userId;

  const load = useCallback(
    async (includeOlder = false) => {
      if (!userId) return;
      if (includeOlder) setLoadingOlder(true);
      else setLoading(true);
      try {
        setPortfolio(await fetchPublicPortfolio(userId, includeOlder));
        setError(null);
        if (includeOlder) setShowOlder(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Portfolio not found');
        setPortfolio(null);
      } finally {
        setLoading(false);
        setLoadingOlder(false);
      }
    },
    [userId],
  );

  useEffect(() => {
    load(false);
  }, [load]);

  useEffect(() => {
    if (!session?.accessToken || !userId || isSelf) return;
    fetchAccountFollowing(session.accessToken)
      .then((rows) => setFollowing(rows.some((r) => r.userId === userId)))
      .catch(() => setFollowing(false));
  }, [session?.accessToken, userId, isSelf]);

  function openIntel(pos: PublicPosition) {
    setIntelPosition({
      ticker: pos.ticker,
      name: pos.name,
      logoUrl: pos.logoUrl,
      priceUsd: pos.priceUsd,
      marketCap: pos.marketCap,
      liquidity: pos.liquidity,
      volume24h: pos.volume24h,
      contractAddress: pos.contractAddress,
      dexscreenerUrl: pos.dexscreenerUrl,
      websiteUrl: pos.websiteUrl,
      twitterUrl: pos.twitterUrl,
      telegramUrl: pos.telegramUrl,
      isDoxxedCurated: pos.isDoxxedCurated,
      founderName: pos.founderName,
      quantity: pos.quantity,
      avgBuyPrice: pos.avgBuyPrice,
      pnl: pos.pnl,
      pnlPercent: pos.pnlPercent,
      marketValue: pos.marketValue,
    });
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link
              href="/leaderboard"
              className="text-xs text-[var(--color-muted)] hover:text-white"
            >
              ← Rankings
            </Link>
            <SiteBrand className="mt-1 text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Trading journey</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        {loading && (
          <p className="text-sm text-[var(--color-muted)]">Loading trader journey…</p>
        )}

        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-950/20 p-6 text-sm text-red-200">
            {error}
          </div>
        )}

        {portfolio && (
          <>
            <TraderProfileHeader
              portfolio={portfolio}
              isSelf={isSelf}
              following={following}
              accessToken={session?.accessToken}
              onFollowChange={setFollowing}
            />

            <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
              <h3 className="font-semibold">Open positions</h3>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                Entry, thesis, and conviction — tap for token details
              </p>
              {portfolio.positions.length === 0 ? (
                <p className="mt-4 text-sm text-[var(--color-muted)]">No open positions.</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {portfolio.positions.map((pos) => (
                    <TraderOpenPositionCard
                      key={`${pos.ticker}-${pos.projectId ?? pos.name}`}
                      pos={pos}
                      portfolio={portfolio}
                      accessToken={session?.accessToken}
                      onOpenIntel={openIntel}
                    />
                  ))}
                </ul>
              )}
            </section>

            <TradingTimeline
              events={portfolio.timeline}
              journeyDays={portfolio.journeyDays}
              hasOlderHistory={portfolio.hasOlderHistory}
              olderTradeCount={portfolio.olderTradeCount}
              showOlder={showOlder}
              onShowOlder={() => load(true)}
              loadingOlder={loadingOlder}
            />

            {portfolio.closedTrades.length > 0 && (
              <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
                <h3 className="font-semibold">Closed trades</h3>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  Profit, what-if-I-held, and shareable &ldquo;sold too early&rdquo; moments
                </p>
                <ul className="mt-4 space-y-3">
                  {portfolio.closedTrades.map((trade) => (
                    <TraderClosedTradeCard
                      key={trade.id}
                      trade={trade}
                      userId={portfolio.userId}
                    />
                  ))}
                </ul>
              </section>
            )}

            <SharePortfolio
              userId={portfolio.userId}
              displayName={portfolio.displayName}
              roi={portfolio.roi}
              totalValue={portfolio.totalValue}
              pnl={portfolio.pnl}
              accessToken={session?.accessToken}
              highlightPosition={
                portfolio.positions.find((p) => p.convictionThesis) ?? portfolio.positions[0]
              }
            />

            <div className="flex flex-wrap gap-3 text-sm">
              <Link href="/leaderboard" className="text-[var(--color-accent)] hover:underline">
                Back to rankings →
              </Link>
              <Link href="/feed" className="text-[var(--color-muted)] hover:text-white">
                Trading feed
              </Link>
              <Link href="/paper-trading" className="text-[var(--color-muted)] hover:text-white">
                Paper terminal
              </Link>
            </div>
          </>
        )}
      </div>

      {intelPosition && (
        <CoinIntelligencePanel data={intelPosition} onClose={() => setIntelPosition(null)} />
      )}
    </main>
  );
}

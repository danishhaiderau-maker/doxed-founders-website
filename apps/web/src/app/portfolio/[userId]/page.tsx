'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { formatPercent, formatUsd } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { CoinIntelligencePanel, type CoinIntelData } from '@/components/coin-intelligence-panel';
import { SharePortfolio } from '@/components/share-portfolio';
import { ReputationBadge } from '@/components/landing/project-spotlight';
import { fetchPublicPortfolio, PublicPortfolio } from '@/lib/api';

type PublicPosition = PublicPortfolio['positions'][number];

export default function PublicPortfolioPage() {
  const params = useParams<{ userId: string }>();
  const userId = params.userId;
  const [portfolio, setPortfolio] = useState<PublicPortfolio | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [intelPosition, setIntelPosition] = useState<CoinIntelData | null>(null);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      setPortfolio(await fetchPublicPortfolio(userId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Portfolio not found');
      setPortfolio(null);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

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
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Paper Trading Portfolio</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        {loading && (
          <p className="text-sm text-[var(--color-muted)]">Loading portfolio…</p>
        )}

        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-950/20 p-6 text-sm text-red-200">
            {error}
          </div>
        )}

        {portfolio && (
          <>
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
              <p className="text-sm text-[var(--color-muted)]">Trader</p>
              <h2 className="mt-1 text-2xl font-bold">{portfolio.displayName}</h2>
              <div className="mt-3">
                <ReputationBadge
                  points={portfolio.reputationPoints}
                  level={portfolio.contributorLevel}
                />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Portfolio" value={formatUsd(portfolio.totalValue)} />
                <Stat
                  label="P&amp;L"
                  value={formatUsd(portfolio.pnl)}
                  accent={portfolio.pnl >= 0 ? 'green' : 'red'}
                />
                <Stat
                  label="ROI"
                  value={formatPercent(portfolio.roi)}
                  accent={portfolio.roi >= 0 ? 'green' : 'red'}
                />
                <Stat label="Positions" value={String(portfolio.positionCount)} />
              </div>
            </div>

            <SharePortfolio
              userId={portfolio.userId}
              displayName={portfolio.displayName}
              roi={portfolio.roi}
              totalValue={portfolio.totalValue}
            />

            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
              <h3 className="font-semibold">Open positions</h3>
              <p className="mt-1 text-xs text-[var(--color-muted)]">Tap a position for token details</p>
              {portfolio.positions.length === 0 ? (
                <p className="mt-3 text-sm text-[var(--color-muted)]">No open positions.</p>
              ) : (
                <ul className="mt-3 space-y-3">
                  {portfolio.positions.map((pos) => (
                    <li
                      key={`${pos.ticker}-${pos.projectId ?? pos.name}`}
                      className="flex cursor-pointer items-center justify-between rounded-lg bg-[var(--color-background)] p-3 text-sm transition hover:ring-1 hover:ring-[var(--color-accent)]/40"
                      onClick={() => openIntel(pos)}
                      onKeyDown={(e) => e.key === 'Enter' && openIntel(pos)}
                      role="button"
                      tabIndex={0}
                    >
                      <div className="flex items-center gap-3">
                        {pos.logoUrl ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={pos.logoUrl} alt="" className="h-8 w-8 rounded-full" />
                        ) : (
                          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--color-border)] text-xs font-bold">
                            {pos.ticker.slice(0, 2)}
                          </div>
                        )}
                        <div>
                          <p className="font-medium">{pos.ticker}</p>
                          <p className="text-xs text-[var(--color-muted)]">{pos.name}</p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="font-medium">{formatUsd(pos.marketValue)}</p>
                        <p
                          className={`text-xs ${
                            pos.pnl >= 0
                              ? 'text-[var(--color-success)]'
                              : 'text-[var(--color-danger)]'
                          }`}
                        >
                          {formatUsd(pos.pnl)} ({formatPercent(pos.pnlPercent)})
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="flex flex-wrap gap-3 text-sm">
              <Link
                href="/feed"
                className="text-[var(--color-accent)] hover:underline"
              >
                View trading feed →
              </Link>
              <Link
                href="/paper-trading"
                className="text-[var(--color-muted)] hover:text-white"
              >
                Open paper terminal
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

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'green' | 'red';
}) {
  const color =
    accent === 'green'
      ? 'text-[var(--color-success)]'
      : accent === 'red'
        ? 'text-[var(--color-danger)]'
        : 'text-white';

  return (
    <div className="rounded-lg bg-[var(--color-background)] p-3">
      <p className="text-xs text-[var(--color-muted)]">{label}</p>
      <p className={`mt-1 font-semibold ${color}`}>{value}</p>
    </div>
  );
}

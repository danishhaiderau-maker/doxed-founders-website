'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { TradingChart } from '@/components/trading-chart';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { BustPenaltyModal } from '@/components/trade-modals';
import { TradeAccountabilityModal } from '@/components/trade-accountability-modal';
import { CoinIntelligencePanel, type CoinIntelData } from '@/components/coin-intelligence-panel';
import { SharePortfolio, SharePosition } from '@/components/share-portfolio';
import { formatUsd, formatPercent, formatPublicAccountLabel, formatTokenPrice } from '@dcf/utils';
import { AccountWelcome } from '@/components/account-welcome';
import {
  createPaperSession,
  createResetCheckout,
  DexScreenerPreview,
  executePaperTrade,
  fetchPaperPortfolio,
  fetchResetInfo,
  PaperPortfolio,
  migrateGuestPortfolio,
  previewPaperTrade,
  resetPaperPortfolio,
} from '@/lib/api';

const SESSION_KEY = 'dcf-paper-user-id';

type Position = PaperPortfolio['positions'][number];

export default function PaperTradingPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen px-6 py-16 text-[var(--color-muted)]">
          Loading terminal…
        </main>
      }
    >
      <PaperTradingPageContent />
    </Suspense>
  );
}

function PaperTradingPageContent() {
  const searchParams = useSearchParams();
  const { data: session, status: authStatus } = useSession();
  const [userId, setUserId] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [dexUrl, setDexUrl] = useState('');
  const [preview, setPreview] = useState<DexScreenerPreview | null>(null);
  const [amountUsd, setAmountUsd] = useState('500');
  const [tradeComment, setTradeComment] = useState('');
  const [tradeCatalyst, setTradeCatalyst] = useState('');
  const [tradeTargetUsd, setTradeTargetUsd] = useState('');
  const [tradeTimeHorizon, setTradeTimeHorizon] = useState('');
  const [lastFeedPostId, setLastFeedPostId] = useState<string | null>(null);
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetInfo, setResetInfo] = useState<{
    message: string;
    stripeEnabled: boolean;
    cryptoEnabled?: boolean;
  } | null>(null);
  const [activeChartUrl, setActiveChartUrl] = useState<string | null>(null);
  const [guestPortfolioNotice, setGuestPortfolioNotice] = useState<string | null>(null);
  const [migrationDone, setMigrationDone] = useState(false);
  const [showAccountabilityModal, setShowAccountabilityModal] = useState(false);
  const [showBustModal, setShowBustModal] = useState(false);
  const [founderDoxxedTick, setFounderDoxxedTick] = useState(false);
  const [intelPosition, setIntelPosition] = useState<CoinIntelData | null>(null);
  const [resetLoading, setResetLoading] = useState(false);
  const [bustDismissed, setBustDismissed] = useState(false);

  const chartUrl = activeChartUrl ?? preview?.dexscreenerUrl ?? null;
  const chartChain = preview?.chainSlug ?? portfolio?.positions[0]?.chainSlug ?? null;
  const chartPair = preview?.pairAddress ?? null;

  const refreshPortfolio = useCallback(async (id: string) => {
    const data = await fetchPaperPortfolio(id);
    setPortfolio(data);
  }, []);

  useEffect(() => {
    if (authStatus === 'loading') return;

    async function bootstrap() {
      const authUserId = session?.user?.id;
      if (authUserId) {
        const stored = localStorage.getItem(SESSION_KEY);
        if (stored && stored !== authUserId && !migrationDone) {
          setMigrationDone(true);
          try {
            const result = await migrateGuestPortfolio(stored, authUserId);
            if (result.migrated) {
              setGuestPortfolioNotice(
                `Imported ${result.positionsMerged} position(s) from your guest session into this account.`,
              );
            } else {
              setGuestPortfolioNotice(
                'Your guest-session trades could not be merged (empty or already imported).',
              );
            }
          } catch {
            setGuestPortfolioNotice(
              'Could not merge guest trades automatically. Sign out and use guest mode to access them.',
            );
          }
        } else if (!stored || stored === authUserId) {
          setGuestPortfolioNotice(null);
        }
        setUserId(authUserId);
        localStorage.setItem(SESSION_KEY, authUserId);
        await refreshPortfolio(authUserId);
        return;
      }

      const stored = localStorage.getItem(SESSION_KEY);
      if (stored) {
        setUserId(stored);
        await refreshPortfolio(stored);
      }
    }

    bootstrap().catch(() => {
      setError('Could not load your portfolio. Try refreshing.');
    });
  }, [authStatus, session?.user?.id, refreshPortfolio, migrationDone]);

  useEffect(() => {
    fetchResetInfo()
      .then((info) =>
        setResetInfo({
          message: info.message,
          stripeEnabled: info.stripeEnabled,
          cryptoEnabled: info.cryptoEnabled,
        }),
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    const resetResult = searchParams.get('reset');
    if (resetResult === 'success' && userId) {
      setGuestPortfolioNotice(
        'Payment received — your portfolio is restarting with $10,000. Refresh if balance looks stale.',
      );
      refreshPortfolio(userId).catch(() => {});
      setBustDismissed(true);
      setShowBustModal(false);
    } else if (resetResult === 'cancelled') {
      setGuestPortfolioNotice('Checkout cancelled — you can restart when ready.');
    }
  }, [searchParams, userId, refreshPortfolio]);

  useEffect(() => {
    const dex = searchParams.get('dex');
    if (dex && userId) {
      setDexUrl(dex);
      previewPaperTrade(dex)
        .then((data) => {
          setPreview(data);
          setActiveChartUrl(data.dexscreenerUrl);
        })
        .catch(() => {});
    }
  }, [searchParams, userId]);

  useEffect(() => {
    if (portfolio?.isBusted && !bustDismissed) {
      setShowBustModal(true);
    }
  }, [portfolio?.isBusted, bustDismissed]);

  async function startSession() {
    setError(null);
    setLoading(true);
    try {
      const paperSession = await createPaperSession(displayName || undefined);
      localStorage.setItem(SESSION_KEY, paperSession.userId);
      setUserId(paperSession.userId);
      await refreshPortfolio(paperSession.userId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start session');
    } finally {
      setLoading(false);
    }
  }

  async function loadPreview(url?: string) {
    const target = (url ?? dexUrl).trim();
    if (!target) return;
    setError(null);
    setLoading(true);
    try {
      const data = await previewPaperTrade(target);
      setPreview(data);
      setDexUrl(target);
      setActiveChartUrl(data.dexscreenerUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed');
    } finally {
      setLoading(false);
    }
  }

  async function openPosition(pos: Position, mode: 'SELL' | 'VIEW' | 'INTEL') {
    if (mode === 'INTEL') {
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
      return;
    }

    const input = pos.dexscreenerUrl ?? pos.contractAddress;
    if (!input) {
      setError(`No DexScreener link or contract stored for ${pos.ticker}. Paste manually to trade.`);
      return;
    }
    setSide(mode === 'SELL' ? 'SELL' : 'BUY');
    setActiveChartUrl(pos.dexscreenerUrl ?? null);
    await loadPreview(input);
    if (mode === 'SELL') {
      setTradeComment('');
      const maxSell =
        Math.floor(pos.quantity * pos.priceUsd * 999) / 1000;
      setAmountUsd(String(Math.max(0.01, maxSell)));
    }
  }

  function buildTradeComment(): string | undefined {
    let comment = tradeComment.trim();
    if (founderDoxxedTick) {
      const note = '[Trader marks founder as publicly doxxed]';
      comment = comment ? `${comment}\n\n${note}` : note;
    }
    return comment || undefined;
  }

  async function executeTrade() {
    if (!userId || !preview) return;
    setError(null);
    setLoading(true);
    try {
      const result = await executePaperTrade({
        userId,
        dexscreenerUrl: preview.dexscreenerUrl,
        side,
        amountUsd: Number(amountUsd),
        comment: buildTradeComment(),
        catalyst: tradeCatalyst.trim() || undefined,
        targetUsd: tradeTargetUsd.trim() ? Number(tradeTargetUsd) : undefined,
        timeHorizon: tradeTimeHorizon.trim() || undefined,
      });
      setLastFeedPostId(result.feedPostId);
      setTradeComment('');
      setTradeCatalyst('');
      setTradeTargetUsd('');
      setTradeTimeHorizon('');
      setFounderDoxxedTick(false);
      setShowAccountabilityModal(false);
      await refreshPortfolio(userId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Trade failed');
    } finally {
      setLoading(false);
    }
  }

  function handleTradeClick() {
    if (!preview) return;
    if (side === 'BUY') {
      setShowAccountabilityModal(true);
      return;
    }
    executeTrade();
  }

  function handleConfirmBuy() {
    setShowAccountabilityModal(false);
    executeTrade();
  }

  async function handlePayReset() {
    if (!userId) return;
    setResetLoading(true);
    setError(null);
    try {
      if (resetInfo?.stripeEnabled) {
        const checkout = await createResetCheckout(userId);
        window.location.href = checkout.url;
        return;
      }
      const result = await resetPaperPortfolio(userId);
      setShowBustModal(false);
      setBustDismissed(true);
      await refreshPortfolio(userId);
      setGuestPortfolioNotice(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed');
    } finally {
      setResetLoading(false);
    }
  }

  const isLoggedIn = Boolean(session?.user?.id);

  if (!userId && authStatus !== 'loading') {
    return (
      <main className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-lg">
          <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
            ← Home
          </Link>
          <h1 className="mt-8 text-3xl font-bold">Paper Trading Terminal</h1>
          <p className="mt-3 text-[var(--color-muted)]">
            Trade any DexScreener token with $10,000 virtual USD.{' '}
            <Link href="/login" className="text-[var(--color-accent)] hover:underline">
              Sign in
            </Link>{' '}
            to keep one portfolio across devices, or start a guest session below.
          </p>
          <div className="mt-8 space-y-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
            <Link
              href="/login?callbackUrl=/paper-trading"
              className="block w-full rounded-lg border border-[var(--color-accent)]/50 py-3 text-center text-sm font-medium text-white hover:border-[var(--color-accent)]"
            >
              Sign in with email or Google
            </Link>
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-[var(--color-border)]" />
              <span className="text-xs text-[var(--color-muted)]">or guest</span>
              <div className="h-px flex-1 bg-[var(--color-border)]" />
            </div>
            <label className="block text-sm">
              <span className="text-[var(--color-muted)]">Display name (optional)</span>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your trader name"
                className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            {error && <p className="text-sm text-red-300">{error}</p>}
            <button
              type="button"
              onClick={startSession}
              disabled={loading}
              className="w-full rounded-lg bg-[var(--color-accent)] py-3 text-sm font-medium text-white disabled:opacity-50"
            >
              {loading ? 'Starting…' : 'Start guest session — $10,000'}
            </button>
          </div>
        </div>
      </main>
    );
  }

  if (authStatus === 'loading' && !userId) {
    return <main className="min-h-screen px-6 py-16 text-[var(--color-muted)]">Loading…</main>;
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Paper Trading Terminal</h1>
            {isLoggedIn && (
              <div className="mt-2">
                <AccountWelcome
                  name={session?.user?.name}
                  email={session?.user?.email}
                />
              </div>
            )}
            {!isLoggedIn && userId && portfolio && (
              <>
                <div className="mt-2">
                  <AccountWelcome
                    name={portfolio.accountName}
                    email={portfolio.accountEmail}
                    prefix="Playing as"
                  />
                </div>
                <p className="mt-1 text-xs text-amber-300/90">
                  Guest session —{' '}
                  <Link href="/login?callbackUrl=/paper-trading" className="underline hover:text-white">
                    sign in
                  </Link>{' '}
                  to sync your portfolio
                </p>
              </>
            )}
            {!isLoggedIn && userId && !portfolio && (
              <p className="mt-0.5 text-xs text-amber-300/90">
                Guest session —{' '}
                <Link href="/login?callbackUrl=/paper-trading" className="underline hover:text-white">
                  sign in
                </Link>{' '}
                to sync your portfolio
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-4">
            {portfolio && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <StatPill label="Cash" value={formatUsd(portfolio.cashBalance)} />
                <StatPill label="Portfolio" value={formatUsd(portfolio.totalValue)} />
                <StatPill
                  label="P&amp;L"
                  value={formatUsd(portfolio.pnl)}
                  accent={portfolio.pnl >= 0 ? 'green' : 'red'}
                />
                <StatPill
                  label="ROI"
                  value={formatPercent(portfolio.roi)}
                  accent={portfolio.roi >= 0 ? 'green' : 'red'}
                />
              </div>
            )}
            <SiteNav />
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-6 px-6 py-8 lg:grid-cols-5">
        {guestPortfolioNotice && (
          <div
            className={`lg:col-span-5 rounded-xl border px-4 py-3 text-sm ${
              guestPortfolioNotice.includes('Imported')
                ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-100'
                : 'border-amber-500/40 bg-amber-950/20 text-amber-100'
            }`}
          >
            {guestPortfolioNotice}
          </div>
        )}
        <div className="space-y-6 lg:col-span-2">
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
            <h2 className="font-semibold">Trade any token</h2>
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              Paste a DexScreener link or contract address (Solana, Base, ETH…).
            </p>
            <input
              type="text"
              value={dexUrl}
              onChange={(e) => setDexUrl(e.target.value)}
              placeholder="DexScreener URL or contract address (0x… / Solana CA)"
              className="mt-4 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
            <button
              type="button"
              onClick={() => loadPreview()}
              disabled={loading || !dexUrl.trim()}
              className="mt-3 w-full rounded-lg border border-[var(--color-border)] py-2.5 text-sm hover:border-[var(--color-accent)] disabled:opacity-50"
            >
              Load token
            </button>

            {preview && (
              <div className="mt-4 rounded-lg bg-[var(--color-background)] p-4">
                <div className="flex items-center gap-3">
                  {preview.logoUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={preview.logoUrl} alt="" className="h-10 w-10 rounded-full" />
                  )}
                  <div>
                    <p className="font-medium">
                      {preview.projectName} ({preview.ticker})
                    </p>
                    <p className="text-sm text-[var(--color-muted)]">
                      {formatTokenPrice(Number(preview.marketPreview.priceUsd) || null)}
                    </p>
                  </div>
                </div>
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    onClick={() => setSide('BUY')}
                    className={`flex-1 rounded-lg py-2 text-sm font-medium ${
                      side === 'BUY'
                        ? 'bg-[var(--color-success)] text-white'
                        : 'border border-[var(--color-border)] text-[var(--color-muted)]'
                    }`}
                  >
                    Buy
                  </button>
                  <button
                    type="button"
                    onClick={() => setSide('SELL')}
                    className={`flex-1 rounded-lg py-2 text-sm font-medium ${
                      side === 'SELL'
                        ? 'bg-[var(--color-danger)] text-white'
                        : 'border border-[var(--color-border)] text-[var(--color-muted)]'
                    }`}
                  >
                    Sell
                  </button>
                </div>
                <label className="mt-4 block text-sm">
                  <span className="text-[var(--color-muted)]">Amount (USD)</span>
                  <input
                    type="number"
                    min={1}
                    step="0.01"
                    value={amountUsd}
                    onChange={(e) => setAmountUsd(e.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
                  />
                </label>
                {side === 'BUY' && (
                  <p className="mt-3 text-xs text-[var(--color-muted)]">
                    You&apos;ll confirm risks and optional thesis before buying.
                  </p>
                )}
                {side === 'SELL' && (
                  <label className="mt-4 block text-sm">
                    <span className="text-[var(--color-muted)]">
                      Why are you closing? (optional — shows on feed)
                    </span>
                    <textarea
                      value={tradeComment}
                      onChange={(e) => setTradeComment(e.target.value)}
                      rows={2}
                      placeholder="What changed your mind?"
                      className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
                    />
                  </label>
                )}
                {!preview.isDoxxedCurated && side === 'BUY' && (
                  <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
                    ⚠️ Non-doxxed token — risk review required before buying.
                  </p>
                )}
                {preview.isDoxxedCurated && side === 'BUY' && (
                  <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-200">
                    ✓ Verified doxxed-founder project
                  </p>
                )}
                <button
                  type="button"
                  onClick={handleTradeClick}
                  disabled={loading}
                  className={`mt-4 w-full rounded-lg py-2.5 text-sm font-medium text-white disabled:opacity-50 ${
                    side === 'SELL' ? 'bg-[var(--color-danger)]' : 'bg-[var(--color-accent)]'
                  }`}
                >
                  {loading ? 'Processing…' : `${side === 'BUY' ? 'Buy' : 'Sell'} ${preview.ticker}`}
                </button>
              </div>
            )}
          </div>

          {lastFeedPostId && (
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-4 text-sm">
              Trade posted to the feed.{' '}
              <Link href="/feed" className="font-medium text-emerald-400 hover:underline">
                View discussion →
              </Link>
            </div>
          )}

          {portfolio?.isBusted && (
            <div className="rounded-xl border border-red-500/40 bg-red-950/20 p-4 text-sm">
              <p className="font-medium text-red-200">💀 Portfolio wiped</p>
              <p className="mt-2 text-[var(--color-muted)]">
                Cash below $1,000. Top up for $25 to restore $10,000 virtual cash.
              </p>
              <button
                type="button"
                onClick={() => setShowBustModal(true)}
                className="mt-3 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500"
              >
                Top up $25 &amp; continue
              </button>
            </div>
          )}

          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
            <h2 className="font-semibold">Your positions</h2>
            {!portfolio?.positions.length && (
              <p className="mt-3 text-sm text-[var(--color-muted)]">No open positions yet.</p>
            )}
            <ul className="mt-3 space-y-3">
              {portfolio?.positions.map((pos) => (
                <li
                  key={pos.projectId}
                  className={`cursor-pointer rounded-lg bg-[var(--color-background)] p-3 text-sm transition hover:ring-1 hover:ring-[var(--color-accent)]/40 ${
                    pos.pnl < 0 ? 'position-loss-glow border border-red-500/30' : ''
                  } ${pos.pnl > 0 ? 'border border-emerald-500/20' : ''}`}
                  onClick={() => openPosition(pos, 'INTEL')}
                  onKeyDown={(e) => e.key === 'Enter' && openPosition(pos, 'INTEL')}
                  role="button"
                  tabIndex={0}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <span className="font-medium">{pos.ticker}</span>
                      <p className="text-xs text-[var(--color-muted)]">{pos.name}</p>
                    </div>
                    <span className="font-medium">{formatUsd(pos.marketValue)}</span>
                  </div>
                  <p className="mt-2 text-xs text-[var(--color-muted)]">
                    {pos.quantity.toFixed(4)} tokens @ {formatTokenPrice(pos.avgBuyPrice)}
                  </p>
                  <p className="mt-1 text-[10px] text-[var(--color-accent)]">Tap for coin intel →</p>
                  <p className="mt-1 text-xs">
                    <span
                      className={
                        pos.pnl >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
                      }
                    >
                      {formatUsd(pos.pnl)} ({formatPercent(pos.pnlPercent)})
                    </span>
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
                    {userId && (
                      <SharePosition
                        userId={userId}
                        projectId={pos.projectId}
                        accessToken={session?.accessToken}
                        displayName={formatPublicAccountLabel(
                          isLoggedIn ? session?.user?.name : portfolio?.accountName,
                          isLoggedIn ? session?.user?.email : portfolio?.accountEmail,
                        )}
                        ticker={pos.ticker}
                        projectName={pos.name}
                        investedUsd={pos.quantity * pos.avgBuyPrice}
                        pnlUsd={pos.pnl}
                        pnlPercent={pos.pnlPercent}
                        entryPrice={pos.avgBuyPrice}
                        currentPrice={pos.priceUsd}
                        thesis={pos.convictionThesis}
                        catalyst={pos.convictionCatalyst}
                        targetPrice={pos.convictionTargetUsd}
                        timeHorizon={pos.convictionTimeHorizon}
                        recordedAt={pos.convictionRecordedAt}
                        positionOpenedAt={pos.positionOpenedAt}
                        portfolioRoi={portfolio?.roi}
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => openPosition(pos, 'SELL')}
                      disabled={loading}
                      className="rounded-md bg-[var(--color-danger)]/90 px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-danger)] disabled:opacity-50"
                    >
                      Close position
                    </button>
                    <button
                      type="button"
                      onClick={() => openPosition(pos, 'VIEW')}
                      disabled={loading}
                      className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-muted)] hover:text-white disabled:opacity-50"
                    >
                      View chart
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {portfolio && userId && (
            <SharePortfolio
              userId={userId}
              accessToken={session?.accessToken}
              displayName={formatPublicAccountLabel(
                isLoggedIn ? session?.user?.name : portfolio.accountName,
                isLoggedIn ? session?.user?.email : portfolio.accountEmail,
              )}
              roi={portfolio.roi}
              totalValue={portfolio.totalValue}
              highlightPosition={
                portfolio.positions.find((p) => p.convictionThesis) ?? portfolio.positions[0]
              }
            />
          )}
        </div>

        <div className="lg:col-span-3">
          {chartUrl ? (
            <TradingChart
              dexscreenerUrl={chartUrl}
              chainSlug={chartChain}
              pairAddress={chartPair}
              height={520}
            />
          ) : (
            <div className="flex h-[520px] items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-card)] p-6 text-center text-[var(--color-muted)]">
              Load a DexScreener link or click &ldquo;View chart&rdquo; on a position
            </div>
          )}
        </div>
      </div>

      {error && (
        <p className="mx-auto mb-8 max-w-6xl px-6 text-sm text-red-300">{error}</p>
      )}

      {preview && portfolio && (
        <TradeAccountabilityModal
          open={showAccountabilityModal}
          preview={preview}
          amountUsd={Number(amountUsd)}
          cashBalance={portfolio.cashBalance}
          resetFeeUsd={portfolio.resetFeeUsd ?? 25}
          thesis={tradeComment}
          onThesisChange={setTradeComment}
          catalyst={tradeCatalyst}
          onCatalystChange={setTradeCatalyst}
          targetUsd={tradeTargetUsd}
          onTargetUsdChange={setTradeTargetUsd}
          timeHorizon={tradeTimeHorizon}
          onTimeHorizonChange={setTradeTimeHorizon}
          founderDoxxedTick={founderDoxxedTick}
          onFounderDoxxedTickChange={setFounderDoxxedTick}
          onCancel={() => setShowAccountabilityModal(false)}
          onConfirm={handleConfirmBuy}
        />
      )}

      {intelPosition && (
        <CoinIntelligencePanel data={intelPosition} onClose={() => setIntelPosition(null)} />
      )}

      <BustPenaltyModal
        open={showBustModal}
        resetFeeUsd={portfolio?.resetFeeUsd ?? 25}
        stripeEnabled={resetInfo?.stripeEnabled ?? false}
        cryptoEnabled={resetInfo?.cryptoEnabled ?? false}
        accessToken={session?.accessToken}
        loading={resetLoading}
        onClose={() => {
          setShowBustModal(false);
          setBustDismissed(true);
        }}
        onPayReset={handlePayReset}
        onCryptoSuccess={async (message) => {
          setShowBustModal(false);
          setBustDismissed(true);
          if (userId) await refreshPortfolio(userId);
          setGuestPortfolioNotice(message);
        }}
      />
    </main>
  );
}

function StatPill({
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
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5">
      <span className="text-[var(--color-muted)]">{label}: </span>
      <span className={`font-medium ${color}`}>{value}</span>
    </div>
  );
}

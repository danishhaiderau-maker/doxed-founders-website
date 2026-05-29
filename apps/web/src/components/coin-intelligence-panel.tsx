'use client';

import Link from 'next/link';
import {
  computeTokenRiskScore,
  formatTokenPrice,
  formatUsd,
  formatPercent,
} from '@dcf/utils';

export type CoinIntelData = {
  ticker: string;
  name: string;
  logoUrl?: string | null;
  priceUsd?: number | null;
  marketCap?: number | null;
  liquidity?: number | null;
  volume24h?: number | null;
  contractAddress?: string | null;
  dexscreenerUrl?: string | null;
  websiteUrl?: string | null;
  twitterUrl?: string | null;
  telegramUrl?: string | null;
  isDoxxedCurated?: boolean;
  founderName?: string | null;
  avgBuyPrice?: number;
  quantity?: number;
  pnl?: number;
  pnlPercent?: number;
  marketValue?: number;
};

function normalizeTwitter(url: string | null | undefined): string | null {
  if (!url?.trim()) return null;
  const t = url.trim();
  if (t.startsWith('http')) return t;
  return `https://x.com/${t.replace(/^@/, '')}`;
}

export function CoinIntelligencePanel({
  data,
  onClose,
}: {
  data: CoinIntelData;
  onClose: () => void;
}) {
  const risk = computeTokenRiskScore({
    isDoxxedCurated: data.isDoxxedCurated,
    liquidityUsd: data.liquidity,
    marketCap: data.marketCap,
    volume24h: data.volume24h,
    hasWebsite: Boolean(data.websiteUrl),
    hasTwitter: Boolean(data.twitterUrl),
  });

  const xUrl = normalizeTwitter(data.twitterUrl);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
      <button type="button" className="absolute inset-0 bg-black/70" aria-label="Close" onClick={onClose} />
      <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            {data.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={data.logoUrl} alt="" className="h-12 w-12 rounded-full" />
            ) : (
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-border)] text-sm font-bold">
                {data.ticker.slice(0, 2)}
              </div>
            )}
            <div>
              <h3 className="text-lg font-bold">
                {data.ticker}{' '}
                <span className="text-sm font-normal text-[var(--color-muted)]">{data.name}</span>
              </h3>
              <p className="text-sm text-[var(--color-muted)]">
                {formatTokenPrice(data.priceUsd ?? null)}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-[var(--color-muted)] hover:text-white">
            ×
          </button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
          <Stat label="Market cap" value={data.marketCap != null ? formatUsd(data.marketCap, 0) : '—'} />
          <Stat label="Liquidity" value={data.liquidity != null ? formatUsd(data.liquidity, 0) : '—'} />
          <Stat label="24h volume" value={data.volume24h != null ? formatUsd(data.volume24h, 0) : '—'} />
          <Stat
            label="Risk score"
            value={`${risk.score}/10 · ${risk.label}`}
            accent={risk.score >= 7 ? 'red' : risk.score <= 3 ? 'green' : undefined}
          />
        </div>

        {data.quantity != null && (
          <div className="mt-4 rounded-lg bg-[var(--color-background)] p-3 text-sm">
            <p className="text-xs uppercase tracking-wider text-[var(--color-muted)]">Your position</p>
            <p className="mt-1">
              {data.quantity.toFixed(4)} tokens @ {formatTokenPrice(data.avgBuyPrice ?? null)}
            </p>
            {data.pnl != null && data.pnlPercent != null && (
              <p
                className={`mt-1 font-medium ${
                  data.pnl >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
                }`}
              >
                {formatUsd(data.pnl)} ({formatPercent(data.pnlPercent)})
              </p>
            )}
          </div>
        )}

        <div className="mt-4 rounded-lg border border-[var(--color-border)] p-3 text-sm">
          <p className="text-xs uppercase tracking-wider text-[var(--color-muted)]">Founder status</p>
          {data.isDoxxedCurated ? (
            <p className="mt-1 text-emerald-300">
              ✅ Verified doxxed founder{data.founderName ? ` · ${data.founderName}` : ''}
            </p>
          ) : (
            <p className="mt-1 text-amber-200">
              ⚠️ Not a verified doxxed-founder project — higher anonymous-team risk.
            </p>
          )}
        </div>

        {data.contractAddress && (
          <p className="mt-3 break-all text-xs text-[var(--color-muted)]">
            Contract: <span className="text-white">{data.contractAddress}</span>
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {data.websiteUrl && (
            <a
              href={data.websiteUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs hover:border-[var(--color-accent)]"
            >
              Website
            </a>
          )}
          {xUrl && (
            <a
              href={xUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-sky-500/40 px-3 py-1.5 text-xs text-sky-300 hover:text-white"
            >
              X / Twitter
            </a>
          )}
          {data.telegramUrl && (
            <a
              href={data.telegramUrl.startsWith('http') ? data.telegramUrl : `https://t.me/${data.telegramUrl}`}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs"
            >
              Telegram
            </a>
          )}
          {data.dexscreenerUrl && (
            <a
              href={data.dexscreenerUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-[var(--color-accent)]/40 px-3 py-1.5 text-xs text-[var(--color-accent)]"
            >
              DexScreener
            </a>
          )}
        </div>

        <Link
          href={data.dexscreenerUrl ? `/paper-trading?dex=${encodeURIComponent(data.dexscreenerUrl)}` : '/paper-trading'}
          className="mt-4 block text-center text-sm text-[var(--color-accent)] hover:underline"
          onClick={onClose}
        >
          Trade this token →
        </Link>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'red' | 'green';
}) {
  const color =
    accent === 'red'
      ? 'text-red-300'
      : accent === 'green'
        ? 'text-emerald-300'
        : 'text-white';
  return (
    <div className="rounded-lg bg-[var(--color-background)] p-2.5">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-muted)]">{label}</p>
      <p className={`mt-0.5 text-sm font-medium ${color}`}>{value}</p>
    </div>
  );
}

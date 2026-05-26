'use client';

import { buildDexScreenerEmbedUrl, buildGeckoTerminalEmbedUrl } from '@dcf/utils';

interface TradingChartProps {
  dexscreenerUrl: string;
  chainSlug?: string | null;
  pairAddress?: string | null;
  height?: number;
  className?: string;
}

export function TradingChart({
  dexscreenerUrl,
  chainSlug,
  pairAddress,
  height = 520,
  className = '',
}: TradingChartProps) {
  const pool =
    pairAddress?.trim() ||
    dexscreenerUrl.match(/dexscreener\.com\/[a-z0-9_-]+\/([a-zA-Z0-9]+)/i)?.[1] ||
    null;

  // Prefer DexScreener embed — same link the user traded from, always resolves.
  const src = buildDexScreenerEmbedUrl(dexscreenerUrl);

  return (
    <div
      className={`overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] ${className}`}
    >
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-muted)]">
        <span>Live chart</span>
        {pool && chainSlug && (
          <a
            href={buildGeckoTerminalEmbedUrl(chainSlug, pool).replace('?embed=1', '')}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--color-accent)] hover:text-white"
          >
            Open on GeckoTerminal →
          </a>
        )}
      </div>
      <iframe
        title="DexScreener price chart"
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        allow="clipboard-write"
        className="block w-full"
      />
    </div>
  );
}

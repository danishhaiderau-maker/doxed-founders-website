'use client';

import { buildDexScreenerEmbedUrl, buildGeckoTerminalEmbedUrl } from '@dcf/utils';

interface GeckoTerminalChartProps {
  chainSlug: string | null | undefined;
  poolAddress: string;
  dexscreenerUrl?: string | null;
  height?: number;
  className?: string;
}

/**
 * Live price chart. Prefers DexScreener embed when a DexScreener URL exists
 * (most reliable). Falls back to GeckoTerminal pool embed otherwise.
 */
export function GeckoTerminalChart({
  chainSlug,
  poolAddress,
  dexscreenerUrl,
  height = 420,
  className = '',
}: GeckoTerminalChartProps) {
  const pool = poolAddress?.trim();
  const dexUrl = dexscreenerUrl?.trim();

  const useDex = Boolean(dexUrl);
  const src = useDex
    ? buildDexScreenerEmbedUrl(dexUrl!)
    : buildGeckoTerminalEmbedUrl(chainSlug, pool);

  const externalHref = useDex
    ? dexUrl!
    : buildGeckoTerminalEmbedUrl(chainSlug, pool).split('?')[0];

  return (
    <div
      className={`overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] ${className}`}
    >
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-muted)]">
        <span>{useDex ? 'Live chart (DexScreener)' : 'Live chart (GeckoTerminal)'}</span>
        <a
          href={externalHref}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--color-accent)] hover:text-white"
        >
          Open full chart →
        </a>
      </div>
      <iframe
        title="Live price chart"
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        allow="clipboard-write"
        loading="lazy"
        referrerPolicy="no-referrer-when-downgrade"
        className="block w-full bg-[#0a0a0f]"
      />
      {!useDex && (
        <p className="border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-muted)]">
          Gecko pool embed can fail if this pair is not indexed. Paste a DexScreener link for the
          most reliable chart.
        </p>
      )}
    </div>
  );
}

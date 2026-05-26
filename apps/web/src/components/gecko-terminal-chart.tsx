'use client';

import { buildGeckoTerminalEmbedUrl } from '@dcf/utils';

interface GeckoTerminalChartProps {
  chainSlug: string | null | undefined;
  poolAddress: string;
  height?: number;
  className?: string;
}

export function GeckoTerminalChart({
  chainSlug,
  poolAddress,
  height = 420,
  className = '',
}: GeckoTerminalChartProps) {
  const src = buildGeckoTerminalEmbedUrl(chainSlug, poolAddress);

  return (
    <div
      className={`overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] ${className}`}
    >
      <iframe
        title="GeckoTerminal price chart"
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

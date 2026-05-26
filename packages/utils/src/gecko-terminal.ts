const GECKO_NETWORK: Record<string, string> = {
  SOLANA: 'solana',
  ETHEREUM: 'eth',
  POLYGON: 'polygon_pos',
  ARBITRUM: 'arbitrum',
  OPTIMISM: 'optimism',
  BASE: 'base',
  AVALANCHE: 'avax',
  BNB_CHAIN: 'bsc',
};

export function mapGeckoNetwork(chainSlug: string | null | undefined): string {
  if (!chainSlug) return 'solana';
  return GECKO_NETWORK[chainSlug.toUpperCase()] ?? chainSlug.toLowerCase();
}

export function buildGeckoTerminalEmbedUrl(
  chainSlug: string | null | undefined,
  poolAddress: string,
): string {
  const network = mapGeckoNetwork(chainSlug);
  const params = new URLSearchParams({
    embed: '1',
    info: '0',
    swaps: '0',
    grayscale: '0',
    light_chart: '0',
    chart_type: 'price',
    resolution: '1d',
  });
  return `https://www.geckoterminal.com/${network}/pools/${poolAddress}?${params.toString()}`;
}

/** DexScreener embed — opens chart view (not Info tab) in dark mode. */
export function buildDexScreenerEmbedUrl(dexscreenerUrl: string): string {
  try {
    const url = new URL(dexscreenerUrl.trim());
    url.searchParams.set('embed', '1');
    url.searchParams.set('chartTheme', 'dark');
    url.searchParams.set('theme', 'dark');
    url.searchParams.set('info', '0');
    url.searchParams.set('trades', '0');
    url.hash = 'chart';
    return url.toString();
  } catch {
    return dexscreenerUrl.trim();
  }
}

export function extractPoolAddressFromDexUrl(url: string): string | null {
  const match = url.trim().match(/dexscreener\.com\/[a-z0-9_-]+\/([a-zA-Z0-9]+)/i);
  return match?.[1] ?? null;
}

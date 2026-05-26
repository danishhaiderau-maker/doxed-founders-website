export interface GeckoTokenAttributes {
  address: string;
  name: string;
  symbol: string;
  image_url?: string;
}

export interface GeckoPoolAttributes {
  address: string;
  base_token_price_usd?: string;
  market_cap_usd?: string;
  fdv_usd?: string;
  reserve_in_usd?: string;
  volume_usd?: { h24?: string };
  price_change_percentage?: { h24?: string };
}

export interface GeckoPoolResponse {
  data: {
    attributes: GeckoPoolAttributes;
    relationships?: {
      base_token?: { data?: { id: string } };
    };
  };
  included?: Array<{
    id: string;
    type: string;
    attributes: GeckoTokenAttributes;
  }>;
}

/** DexScreener URL chain segment → GeckoTerminal network slug */
export const DEXSCREENER_CHAIN_TO_GECKO: Record<string, string> = {
  solana: 'solana',
  ethereum: 'eth',
  eth: 'eth',
  polygon: 'polygon_pos',
  arbitrum: 'arbitrum',
  optimism: 'optimism',
  base: 'base',
  avalanche: 'avax',
  bsc: 'bsc',
};

export function mapDexScreenerChainToGecko(chainId: string): string | null {
  return DEXSCREENER_CHAIN_TO_GECKO[chainId.toLowerCase()] ?? null;
}

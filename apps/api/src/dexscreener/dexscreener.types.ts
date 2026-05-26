import { ChainSlug } from '@prisma/client';

export interface DexScreenerPairInfo {
  chainId: string;
  url: string;
  pairAddress: string;
  baseToken: {
    address: string;
    name: string;
    symbol: string;
  };
  priceUsd?: string;
  marketCap?: number;
  fdv?: number;
  volume?: { h24?: number };
  liquidity?: { usd?: number };
  priceChange?: { h24?: number };
  info?: {
    imageUrl?: string;
    websites?: { url: string; label?: string }[];
    socials?: { url: string; type: string }[];
  };
}

export interface DexScreenerPreview {
  dexscreenerUrl: string;
  pairAddress?: string;
  projectName: string;
  ticker: string;
  websiteUrl?: string;
  telegramUrl?: string;
  founderTwitter?: string;
  contractAddress: string;
  chainSlug: ChainSlug | null;
  logoUrl?: string;
  summary?: string;
  marketPreview: {
    priceUsd?: string;
    marketCap?: number;
    fdv?: number;
    volume24h?: number;
    liquidityUsd?: number;
    priceChange24h?: number;
  };
}

const CHAIN_MAP: Record<string, ChainSlug> = {
  solana: 'SOLANA',
  ethereum: 'ETHEREUM',
  polygon: 'POLYGON',
  arbitrum: 'ARBITRUM',
  optimism: 'OPTIMISM',
  base: 'BASE',
  avalanche: 'AVALANCHE',
  bsc: 'BNB_CHAIN',
};

export const CHAIN_SLUG_TO_DEX: Record<ChainSlug, string> = {
  SOLANA: 'solana',
  ETHEREUM: 'ethereum',
  POLYGON: 'polygon',
  ARBITRUM: 'arbitrum',
  OPTIMISM: 'optimism',
  BASE: 'base',
  AVALANCHE: 'avalanche',
  BNB_CHAIN: 'bsc',
};

export function parseDexScreenerUrl(url: string): {
  chainId: string;
  address: string;
} | null {
  const trimmed = url.trim();
  const match = trimmed.match(
    /dexscreener\.com\/([a-z0-9_-]+)\/([a-zA-Z0-9]+)/i,
  );
  if (!match) return null;
  return { chainId: match[1].toLowerCase(), address: match[2] };
}

export function mapChainSlug(chainId: string): ChainSlug | null {
  return CHAIN_MAP[chainId.toLowerCase()] ?? null;
}

export function buildPreviewFromPair(
  dexscreenerUrl: string,
  pair: DexScreenerPairInfo,
): DexScreenerPreview {
  const website = pair.info?.websites?.[0]?.url;
  const twitter = pair.info?.socials?.find(
    (s) => s.type === 'twitter' || s.url.includes('x.com') || s.url.includes('twitter.com'),
  )?.url;
  const telegram = pair.info?.socials?.find(
    (s) => s.type === 'telegram' || s.url.includes('t.me'),
  )?.url;

  const chainSlug = mapChainSlug(pair.chainId);

  return {
    dexscreenerUrl,
    pairAddress: pair.pairAddress,
    projectName: pair.baseToken.name,
    ticker: pair.baseToken.symbol,
    websiteUrl: website,
    telegramUrl: telegram,
    founderTwitter: twitter,
    contractAddress: pair.baseToken.address,
    chainSlug,
    logoUrl: pair.info?.imageUrl,
    summary: `${pair.baseToken.name} (${pair.baseToken.symbol}) on ${pair.chainId}. Listed via DexScreener.`,
    marketPreview: {
      priceUsd: pair.priceUsd,
      marketCap: pair.marketCap,
      fdv: pair.fdv,
      volume24h: pair.volume?.h24,
      liquidityUsd: pair.liquidity?.usd,
      priceChange24h: pair.priceChange?.h24,
    },
  };
}

export type ChainSlug =
  | 'ETHEREUM'
  | 'SOLANA'
  | 'POLYGON'
  | 'ARBITRUM'
  | 'OPTIMISM'
  | 'BASE'
  | 'AVALANCHE'
  | 'BNB_CHAIN';

export type UserRole = 'USER' | 'ADMIN';

export type ListingStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface HealthResponse {
  status: string;
  timestamp: string;
  services: {
    api: string;
    database: string;
  };
}

export interface ApiResponse<T> {
  data: T;
  meta?: {
    total?: number;
    page?: number;
    limit?: number;
  };
}

export interface ProjectSummary {
  id: string;
  slug: string;
  name: string;
  ticker: string;
  logoUrl?: string | null;
  chain: { slug: ChainSlug; name: string };
  category?: { slug: string; name: string } | null;
  featured: boolean;
}

export interface FounderSummary {
  id: string;
  slug: string;
  name: string;
  photoUrl?: string | null;
  bio?: string | null;
}

export const SUPPORTED_CHAINS: ChainSlug[] = [
  'ETHEREUM',
  'SOLANA',
  'POLYGON',
  'ARBITRUM',
  'OPTIMISM',
  'BASE',
  'AVALANCHE',
  'BNB_CHAIN',
];

export const PAPER_TRADING_STARTING_BALANCE = 10_000;

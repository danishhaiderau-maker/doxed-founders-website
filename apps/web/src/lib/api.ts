import { apiUrl, describeApiTarget } from './api-base';
import type { GamifiedRole, NotificationPreferenceGroups, SecurityScoreResult } from '@dcf/utils';

export interface DexScreenerPreview {
  dexscreenerUrl: string;
  pairAddress?: string;
  projectName: string;
  ticker: string;
  isDoxxedCurated?: boolean;
  curatedProjectSlug?: string | null;
  websiteUrl?: string;
  telegramUrl?: string;
  founderTwitter?: string;
  contractAddress: string;
  chainSlug: string | null;
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

export interface ListingFormData {
  projectName: string;
  ticker: string;
  websiteUrl?: string;
  docsUrl?: string;
  whitepaperUrl?: string;
  contractAddress?: string;
  chainSlug?: string;
  dexscreenerUrl?: string;
  logoUrl?: string;
  telegramUrl?: string;
  founderName?: string;
  founderLinkedIn?: string;
  founderTwitter?: string;
  founderGithub?: string;
  founderVideoUrl?: string;
  founderInterviewUrl?: string;
  companyDetails?: string;
  auditUrl?: string;
  summary?: string;
  whyList?: string;
  whyDoxxed?: string;
  founderDoxxedStatus?: 'DOXXED' | 'BUILDING_IN_PUBLIC';
  scoutHighlightNote?: string;
  marketPreview?: DexScreenerPreview['marketPreview'];
}

export interface PendingApplication {
  id: string;
  projectName: string;
  ticker: string;
  chainSlug: string | null;
  websiteUrl: string | null;
  docsUrl: string | null;
  whitepaperUrl: string | null;
  contractAddress: string | null;
  dexscreenerUrl: string | null;
  logoUrl: string | null;
  telegramUrl: string | null;
  founderName: string | null;
  founderLinkedIn: string | null;
  founderTwitter: string | null;
  founderGithub: string | null;
  founderVideoUrl: string | null;
  founderInterviewUrl: string | null;
  companyDetails: string | null;
  auditUrl: string | null;
  summary: string | null;
  marketPreview: DexScreenerPreview['marketPreview'] | null;
  verificationScore: number;
  verificationCriteria: string[] | null;
  whyList?: string | null;
  whyDoxxed?: string | null;
  requiredVoters?: number;
  minYesPercent?: number;
  votingClosesAt?: string | null;
  status: string;
  createdAt: string;
  votes?: ListingVoteRecord[];
}

export interface ListingVoteRecord {
  id: string;
  vote: 'YES' | 'NO';
  whyList: string | null;
  whyDoxxed: string | null;
  comment: string | null;
  createdAt: string;
  user: { id: string; name: string | null; contributorLevel: number };
}

export interface VoteTally {
  total: number;
  yes: number;
  no: number;
  yesPercent: number;
  requiredVoters: number;
  minYesPercent: number;
  passed: boolean;
  remainingVoters: number;
}

export interface ScoutListing {
  id: string;
  projectName: string;
  ticker: string;
  chainSlug: string | null;
  logoUrl: string | null;
  summary: string | null;
  whyList: string | null;
  whyDoxxed: string | null;
  founderName: string | null;
  founderTwitter: string | null;
  founderVideoUrl: string | null;
  verificationScore: number;
  requiredVoters: number;
  minYesPercent: number;
  votingClosesAt: string | null;
  status: string;
  createdAt: string;
  user: {
    id: string;
    name: string | null;
    reputationPoints: number;
    contributorLevel: number;
  } | null;
  votes: ListingVoteRecord[];
  tally: VoteTally;
  platformVoting: {
    activeUsers: number;
    requiredVoters: number;
    minYesPercent: number;
    votingWindowHours: number;
    formula: string;
  };
}

export type AdminApplicationUpdates = Partial<ListingFormData>;

export interface PaperSession {
  userId: string;
  displayName: string;
  cashBalance: number;
  totalValue: number;
  startingCash: number;
}

export interface PaperPortfolio {
  userId: string;
  accountName?: string | null;
  accountEmail?: string | null;
  cashBalance: number;
  totalValue: number;
  pnl: number;
  roi: number;
  startingCash: number;
  isBusted?: boolean;
  resetFeeUsd?: number;
  recentTrades?: PaperRecentTrade[];
  positions: {
    projectId: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
    dexscreenerUrl: string | null;
    contractAddress?: string | null;
    websiteUrl?: string | null;
    chainSlug?: string;
    twitterUrl?: string | null;
    telegramUrl?: string | null;
    isDoxxedCurated?: boolean;
    founderName?: string | null;
    quantity: number;
    avgBuyPrice: number;
    priceUsd: number;
    marketValue: number;
    pnl: number;
    pnlPercent: number;
    marketCap?: number | null;
    liquidity?: number | null;
    volume24h?: number | null;
    convictionThesis?: string | null;
    convictionCatalyst?: string | null;
    convictionTargetUsd?: number | null;
    convictionTimeHorizon?: string | null;
    convictionRecordedAt?: string | null;
    positionOpenedAt?: string | null;
  }[];
}

export interface PaperRecentTrade {
  id: string;
  side: 'BUY' | 'SELL';
  ticker: string;
  projectName: string;
  quantity: number;
  priceUsd: number;
  totalUsd: number;
  realizedPnlUsd: number | null;
  createdAt: string;
}

export interface PaperLimitOrder {
  id: string;
  side: 'BUY' | 'SELL';
  trigger: 'GTE' | 'LTE';
  targetPriceUsd: number;
  amountUsd: number | null;
  sellPercent: number;
  status: 'OPEN' | 'FILLED' | 'CANCELLED' | 'EXPIRED';
  ticker: string | null;
  projectName: string | null;
  dexscreenerUrl: string | null;
  filledAt: string | null;
  createdAt: string;
}

export interface LeaderboardEntry {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle?: string | null;
  totalValue: number;
  pnl: number;
  roi: number;
  period: string;
}

export interface BustedTraderEntry {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle?: string | null;
  totalValue: number;
  pnl: number;
  roi: number;
  isBusted?: boolean;
}

function parseApiError(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'message' in body) {
    const message = (body as { message: unknown }).message;
    if (Array.isArray(message)) {
      return message.join('. ');
    }
    if (typeof message === 'string') {
      return message;
    }
  }
  return `Request failed (${status})`;
}

async function apiFetch<T>(path: string, init?: RequestInit, token?: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new Error(
      `Cannot reach API at ${describeApiTarget()}. Run dev-lan.cmd and ensure the Nest API is on port 4000.`,
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(parseApiError(body, res.status));
  }
  return res.json() as Promise<T>;
}

export function previewDexScreener(url: string) {
  return apiFetch<DexScreenerPreview>('/listing-applications/preview-dexscreener', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
}

export function previewContract(chainSlug: string, contractAddress: string) {
  return apiFetch<DexScreenerPreview>('/listing-applications/preview-contract', {
    method: 'POST',
    body: JSON.stringify({ chainSlug, contractAddress }),
  });
}

export function submitListingApplication(data: ListingFormData, token?: string) {
  return apiFetch<{ id: string; status: string; projectName: string; verificationScore: number }>(
    '/listing-applications',
    { method: 'POST', body: JSON.stringify(data) },
    token,
  );
}

export function updateListingScoutFields(
  id: string,
  data: {
    scoutHighlightNote?: string;
    whyList?: string;
    whyDoxxed?: string;
    founderDoxxedStatus?: 'DOXXED' | 'BUILDING_IN_PUBLIC';
  },
  token: string,
) {
  return apiFetch(`/listing-applications/${id}/scout`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }, token);
}

export function fetchVotingStats() {
  return apiFetch<ScoutListing['platformVoting']>('/listing-applications/voting/stats');
}

export function fetchOpenScoutListings() {
  return apiFetch<ScoutListing[]>('/listing-applications/voting/open');
}

export function fetchScoutListing(id: string) {
  return apiFetch<ScoutListing>(`/listing-applications/voting/${id}`);
}

export function castScoutVote(
  id: string,
  body: {
    vote: 'YES' | 'NO';
    whyList?: string;
    whyDoxxed?: string;
    comment?: string;
  },
  token: string,
) {
  return apiFetch<ScoutListing>(`/listing-applications/voting/${id}/vote`, {
    method: 'POST',
    body: JSON.stringify(body),
  }, token);
}

export function fetchPendingApplications(token: string) {
  return apiFetch<PendingApplication[]>('/listing-applications/pending', undefined, token);
}

export interface ReviewListingResult {
  application: { id: string; status: string; projectName?: string };
  published: {
    projectId: string;
    projectSlug: string;
    projectName: string;
    founderSlug: string | null;
  } | null;
}

export function reviewListingApplication(
  id: string,
  status: 'APPROVED' | 'REJECTED',
  token: string,
  options?: {
    reviewNotes?: string;
    updates?: AdminApplicationUpdates;
  },
) {
  return apiFetch<ReviewListingResult>(
    `/listing-applications/${id}/review`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        status,
        reviewNotes: options?.reviewNotes,
        ...options?.updates,
      }),
    },
    token,
  );
}

export function registerAccount(input: {
  email: string;
  password: string;
  name?: string;
}) {
  return apiFetch<{ accessToken: string; user: { id: string; email: string; role: string } }>(
    '/auth/register',
    { method: 'POST', body: JSON.stringify(input) },
  );
}

export interface LoginApiResponse {
  accessToken?: string;
  user?: { id: string; email: string; name: string | null; role: string };
  requires2fa?: boolean;
  pendingToken?: string;
  methods?: string[];
}

export function loginAccount(input: { email: string; password: string }) {
  return apiFetch<LoginApiResponse>(
    '/auth/login',
    { method: 'POST', body: JSON.stringify(input) },
  );
}

export function createPaperSession(displayName?: string) {
  return apiFetch<PaperSession>('/paper-trading/session', {
    method: 'POST',
    body: JSON.stringify({ displayName }),
  });
}

export function fetchPaperPortfolio(userId: string) {
  return apiFetch<PaperPortfolio>(`/paper-trading/portfolio/${userId}`);
}

export interface PublicPortfolio {
  userId: string;
  displayName: string;
  reputationPoints: number;
  contributorLevel: number;
  cashBalance: number;
  totalValue: number;
  pnl: number;
  roi: number;
  startingCash: number;
  positionCount: number;
  positions: {
    projectId?: string;
    ticker: string;
    name: string;
    logoUrl: string | null;
    dexscreenerUrl?: string | null;
    contractAddress?: string | null;
    websiteUrl?: string | null;
    chainSlug?: string;
    twitterUrl?: string | null;
    telegramUrl?: string | null;
    isDoxxedCurated?: boolean;
    founderName?: string | null;
    quantity?: number;
    avgBuyPrice?: number;
    priceUsd?: number;
    marketValue: number;
    pnl: number;
    pnlPercent: number;
    marketCap?: number | null;
    liquidity?: number | null;
    volume24h?: number | null;
    convictionThesis?: string | null;
    convictionCatalyst?: string | null;
    convictionTargetUsd?: number | null;
    convictionTimeHorizon?: string | null;
    convictionRecordedAt?: string | null;
    positionOpenedAt?: string | null;
  }[];
}

export function fetchPublicPortfolio(userId: string) {
  return apiFetch<PublicPortfolio>(`/paper-trading/portfolio/${userId}/public`);
}

export function previewPaperTrade(url: string) {
  return apiFetch<DexScreenerPreview>('/paper-trading/preview', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
}

export function migrateGuestPortfolio(guestUserId: string, targetUserId: string) {
  return apiFetch<{ migrated: boolean; positionsMerged: number }>(
    '/paper-trading/migrate-guest',
    {
      method: 'POST',
      body: JSON.stringify({ guestUserId, targetUserId }),
    },
  );
}

export function executePaperTrade(input: {
  userId: string;
  dexscreenerUrl: string;
  side: 'BUY' | 'SELL';
  amountUsd: number;
  comment?: string;
  catalyst?: string;
  targetUsd?: number;
  timeHorizon?: string;
}) {
  return apiFetch<{
    success: boolean;
    feedPostId: string;
    ticker: string;
    amountUsd: number;
    realizedPnlUsd?: number | null;
  }>('/paper-trading/trade', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function closePaperPosition(input: {
  userId: string;
  projectId: string;
  comment?: string;
  sellPercent?: number;
}) {
  return apiFetch<{
    success: boolean;
    ticker: string;
    proceedsUsd: number;
    realizedPnlUsd: number;
    feedPostId: string;
    cashBalance: number;
  }>('/paper-trading/close', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function swapPaperTokens(input: {
  userId: string;
  fromProjectId: string;
  toDexscreenerUrl: string;
  comment?: string;
}) {
  return apiFetch<{
    sell: { ticker: string; proceedsUsd: number; realizedPnlUsd: number };
    buy: { ticker: string; amountUsd: number };
  }>('/paper-trading/swap', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function fetchPaperLimitOrders(userId: string) {
  return apiFetch<PaperLimitOrder[]>(`/paper-trading/limit-orders/${userId}`);
}

export function createPaperLimitOrder(input: {
  userId: string;
  side: 'BUY' | 'SELL';
  trigger: 'GTE' | 'LTE';
  targetPriceUsd: number;
  projectId?: string;
  amountUsd?: number;
  sellPercent?: number;
  dexscreenerUrl?: string;
}) {
  return apiFetch<{ id: string; status: string }>('/paper-trading/limit-orders', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function cancelPaperLimitOrder(userId: string, orderId: string) {
  return apiFetch<{ cancelled: boolean }>(`/paper-trading/limit-orders/${orderId}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ userId }),
  });
}

export interface FeedPost {
  id: string;
  paperTradeId: string;
  side: 'BUY' | 'SELL';
  amountUsd: number;
  priceUsd: number;
  initialComment: string | null;
  commentCount: number;
  highlighted: boolean;
  highlightedUntil: string | null;
  createdAt: string;
  trader: { id: string; name: string; avatarUrl: string | null };
  project: {
    id: string;
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
    dexscreenerUrl: string | null;
    chainSlug: string;
    marketCap: number | null;
  };
}

export interface FeedComment {
  id: string;
  body: string;
  createdAt: string;
  user: { id: string; name: string; avatarUrl: string | null };
}

export function fetchFeed(filter: 'recent' | 'discussed' | 'highlighted' = 'recent') {
  return apiFetch<{ filter: string; posts: FeedPost[] }>(`/feed?filter=${filter}`);
}

export type UnifiedFeedCategory = 'all' | 'founder' | 'trading' | 'community' | 'market';

export interface UnifiedFeedItem {
  id: string;
  tier: 1 | 2 | 3;
  category: 'founder' | 'trading' | 'community' | 'market';
  eventType: string;
  headline: string;
  detail?: string;
  at: string;
  link?: string;
  emoji?: string;
  pinned?: boolean;
  sourceUrl?: string;
  tradePostId?: string;
  tradeSide?: 'BUY' | 'SELL';
  traderName?: string;
  projectSlug?: string;
  projectTicker?: string;
  founderSlug?: string;
  amountUsd?: number;
  recentBuyerNames?: string[];
  shareContext?: {
    projectName?: string;
    pctOfActive?: number;
    detailLine?: string;
    scoutHighlight?: string | null;
    scoutThesis?: string | null;
    summary?: string | null;
    communitySnippets?: string[];
  };
}

export interface PlatformPulseItem {
  id: string;
  emoji: string;
  headline: string;
  detail?: string;
  link?: string;
  tier: 1 | 2 | 3;
}

export interface HotPredictionItem {
  id: string;
  question: string;
  projectName: string;
  projectSlug: string;
  projectTicker: string;
  totalPoolUsd: number;
  participantCount: number;
  conviction: number;
  heatLabel: 'Blazing' | 'Heating up' | null;
  hoursLeft: number | null;
}

export interface ScoutListingFeedItem {
  id: string;
  projectName: string;
  ticker: string;
  whyList: string | null;
  voteCount: number;
  at: string;
}

export interface EngagementFlash {
  id: string;
  emoji: string;
  message: string;
  link?: string;
  at: string;
}

export function fetchUnifiedFeed(category: UnifiedFeedCategory = 'all') {
  return apiFetch<{
    category: UnifiedFeedCategory;
    items: UnifiedFeedItem[];
    pulse: PlatformPulseItem[];
    hotQuestions: HotPredictionItem[];
    scoutListings: ScoutListingFeedItem[];
  }>(`/feed/unified?category=${category}`);
}

export function fetchEngagementFlashes(since?: string) {
  const qs = since ? `?since=${encodeURIComponent(since)}` : '';
  return apiFetch<EngagementFlash[]>(`/feed/flashes${qs}`);
}

export function fetchFeedComments(feedPostId: string) {
  return apiFetch<{
    feedPostId: string;
    initialComment: string | null;
    comments: FeedComment[];
  }>(`/feed/${feedPostId}/comments`);
}

export function postFeedComment(feedPostId: string, userId: string, body: string) {
  return apiFetch<FeedComment>(`/feed/${feedPostId}/comments`, {
    method: 'POST',
    body: JSON.stringify({ userId, body }),
  });
}

export function postInitialFeedComment(feedPostId: string, userId: string, body: string) {
  return apiFetch(`/feed/${feedPostId}/initial-comment`, {
    method: 'POST',
    body: JSON.stringify({ userId, body }),
  });
}

export function fetchLeaderboard() {
  return apiFetch<LeaderboardEntry[]>('/paper-trading/leaderboard');
}

export interface ReputationLeaderboardEntry {
  rank: number;
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  reputationPoints: number;
  contributorLevel: number;
  airdropPoolPercent: number;
  supplyPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
}

export interface ReputationMe {
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  reputationPoints: number;
  contributorLevel: number;
  rank: number | null;
  totalParticipants: number;
  totalPoints: number;
  airdropPoolPercent: number;
  supplyPercent: number;
  estimatedTokens: number;
  estimatedUsd: number;
}

export interface ReputationLeaderboardResponse {
  entries: ReputationLeaderboardEntry[];
  totalParticipants: number;
  totalPoints: number;
}

export function fetchReputationLeaderboard(limit = 50) {
  return apiFetch<ReputationLeaderboardResponse>(`/reputation/leaderboard?limit=${limit}`);
}

export function fetchReputationMe(token: string) {
  return apiFetch<ReputationMe>('/reputation/me', undefined, token);
}

export function fetchBustedTraders() {
  return apiFetch<BustedTraderEntry[]>('/paper-trading/busted');
}

export function fetchResetInfo() {
  return apiFetch<{
    available: boolean;
    resetFeeUsd: number;
    restrictedThresholdUsd?: number;
    stripeEnabled: boolean;
    cryptoEnabled?: boolean;
    treasuryAddress?: string | null;
    message: string;
  }>('/paper-trading/reset-info');
}

export interface CryptoTopUpIntent {
  paymentId: string;
  reference: string;
  asset: 'USDC' | 'SOL';
  amountUsd: number;
  treasuryAddress: string;
  payerAddress: string;
  memo: string;
  expiresAt: string;
  instructions: string;
}

export function createCryptoTopUpIntent(token: string, asset: 'USDC' | 'SOL' = 'USDC') {
  return apiFetch<CryptoTopUpIntent>(
    '/paper-trading/crypto/intent',
    { method: 'POST', body: JSON.stringify({ asset }) },
    token,
  );
}

export function confirmCryptoTopUp(paymentId: string, txSignature: string, token: string) {
  return apiFetch<{
    success: boolean;
    message: string;
    cashBalance: number;
    reference?: string;
    txSignature?: string;
    payerAddress?: string;
  }>(
    '/paper-trading/crypto/confirm',
    { method: 'POST', body: JSON.stringify({ paymentId, txSignature }) },
    token,
  );
}

export function createResetCheckout(userId: string) {
  return apiFetch<{ url: string; sessionId: string }>('/paper-trading/checkout/reset', {
    method: 'POST',
    body: JSON.stringify({ userId }),
  });
}

export function resetPaperPortfolio(userId: string) {
  return apiFetch<{
    success: boolean;
    resetFeeUsd: number;
    message: string;
    cashBalance: number;
  }>('/paper-trading/reset', {
    method: 'POST',
    body: JSON.stringify({ userId }),
  });
}

export interface ProjectMetrics {
  priceUsd: number | null;
  marketCap: number | null;
  fdv: number | null;
  volume24h: number | null;
  liquidity: number | null;
  holders: number | null;
  priceChange24h: number | null;
  updatedAt?: string;
}

export interface ProjectSummary {
  slug: string;
  name: string;
  ticker: string;
  summary: string | null;
  logoUrl: string | null;
  websiteUrl: string | null;
  dexscreenerUrl: string | null;
  featured: boolean;
  source: string;
  listingKind?: 'verified' | 'founder_os' | 'paper_track';
  isVerifiedListing?: boolean;
  lifecycleStage?: string;
  launchReadiness?: number;
  bubbleScore?: number;
  isLiveToken?: boolean;
  followerCount?: number;
  founderScore?: number;
  buildStreakDays?: number;
  simulatedDemand?: number;
  chain: { slug: string; name: string };
  category: { slug: string; name: string } | null;
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    verifications: string[];
  } | null;
  metrics: ProjectMetrics | null;
  scoutHighlight?: string | null;
  founderDoxxedStatus?: 'DOXXED' | 'BUILDING_IN_PUBLIC' | null;
  listingScoutThesis?: string | null;
}

export interface ProjectDetail extends ProjectSummary {
  description: string | null;
  docsUrl: string | null;
  whitepaperUrl: string | null;
  contractAddress: string | null;
  recentPaperBuyers?: Array<{
    userId: string;
    displayName: string;
    amountUsd: number;
    twitterHandle?: string | null;
  }>;
  socials: {
    twitterUrl: string | null;
    discordUrl: string | null;
    telegramUrl: string | null;
    githubUrl: string | null;
    mediumUrl: string | null;
  } | null;
  documents: { id: string; title: string; url: string; type: string | null }[];
  auditReports: { auditor: string; reportUrl: string; auditedAt: string | null }[];
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    linkedInUrl: string | null;
    twitterUrl: string | null;
    githubUrl: string | null;
    verifications: string[];
  } | null;
  verificationDossier?: {
    founderName?: string | null;
    founderTwitter?: string | null;
    founderLinkedIn?: string | null;
    founderGithub?: string | null;
    founderVideoUrl?: string | null;
    founderInterviewUrl?: string | null;
    companyDetails?: string | null;
    whyList?: string | null;
    whyDoxxed?: string | null;
    verificationScore?: number | null;
    verificationCriteria?: string[] | null;
    websiteUrl?: string | null;
    telegramUrl?: string | null;
    auditUrl?: string | null;
  } | null;
}

export interface FounderSummary {
  slug: string;
  name: string;
  bio: string | null;
  photoUrl: string | null;
  linkedInUrl: string | null;
  twitterUrl: string | null;
  githubUrl: string | null;
  videoUrl: string | null;
  verifications: string[];
  projectCount: number;
}

export interface FounderDetail extends Omit<FounderSummary, 'projectCount'> {
  projects: ProjectSummary[];
}

export function fetchProjects(params?: { featured?: boolean; category?: string }) {
  const qs = new URLSearchParams();
  if (params?.featured) qs.set('featured', 'true');
  if (params?.category) qs.set('category', params.category);
  const query = qs.toString();
  return apiFetch<ProjectSummary[]>(`/projects${query ? `?${query}` : ''}`);
}

export function fetchFeaturedProjects() {
  return apiFetch<ProjectSummary[]>('/projects/featured/list');
}

export interface PlatformStats {
  verifiedFounders: number;
  activeProjects: number;
  communityMembers: number;
  simulatedCapital: number;
  paperTraders: number;
  totalTrades: number;
}

export function fetchPlatformStats() {
  return apiFetch<PlatformStats>('/projects/platform/stats');
}

export interface PlatformActivityItem {
  id: string;
  kind: 'build' | 'video' | 'demand';
  founderName: string;
  founderSlug?: string;
  projectSlug?: string;
  projectName?: string;
  headline: string;
  detail?: string;
  amountUsd?: number;
  at: string;
}

export function fetchPlatformActivity(limit = 10) {
  return apiFetch<PlatformActivityItem[]>(`/projects/platform/activity?limit=${limit}`);
}

export function fetchProject(slug: string) {
  return apiFetch<ProjectDetail>(`/projects/${slug}`);
}

export function fetchFounders() {
  return apiFetch<FounderSummary[]>('/founders');
}

export function fetchFounder(slug: string) {
  return apiFetch<FounderDetail>(`/founders/${slug}`);
}

export function fetchWatchlist(token: string) {
  return apiFetch<ProjectSummary[]>('/watchlist', undefined, token);
}

export function fetchWatchlistSlugs(token: string) {
  return apiFetch<{ slugs: string[] }>('/watchlist/slugs', undefined, token);
}

export function addToWatchlist(slug: string, token: string) {
  return apiFetch<{ saved: boolean; slug: string }>(`/watchlist/${slug}`, {
    method: 'POST',
  }, token);
}

export function removeFromWatchlist(slug: string, token: string) {
  return apiFetch<{ saved: boolean; slug: string }>(`/watchlist/${slug}`, {
    method: 'DELETE',
  }, token);
}

export interface SpotlightProject {
  slug: string;
  name: string;
  ticker: string;
  summary: string | null;
  logoUrl: string | null;
  chain: { slug: string; name: string };
  founder: {
    slug: string;
    name: string;
    twitterUrl: string | null;
    videoUrl: string | null;
  } | null;
  socials: {
    twitterUrl: string | null;
  } | null;
  metrics: ProjectMetrics | null;
}

export interface FounderUpdate {
  id: string;
  sourceUrl: string;
  headline: string;
  summary: string | null;
  publishedAt: string;
  project: {
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
  } | null;
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    twitterUrl: string | null;
  } | null;
}

export interface AppNotification {
  id: string;
  type: string;
  title: string;
  body: string;
  link: string | null;
  metadata?: Record<string, unknown> | null;
  readAt: string | null;
  createdAt: string;
}

export function fetchSpotlightProjects() {
  return apiFetch<SpotlightProject[]>('/founder-updates/spotlight');
}

export function fetchPinnedFounderUpdates() {
  return apiFetch<FounderUpdate[]>('/founder-updates/pinned');
}

export function fetchNotifications(token: string, category?: string) {
  const q = category ? `?category=${encodeURIComponent(category)}` : '';
  return apiFetch<AppNotification[]>(`/notifications${q}`, undefined, token);
}

export function fetchUnreadNotificationCount(token: string) {
  return apiFetch<{ count: number }>('/notifications/unread-count', undefined, token);
}

export function markNotificationRead(id: string, token: string) {
  return apiFetch(`/notifications/${id}/read`, { method: 'PATCH' }, token);
}

export function markAllNotificationsRead(token: string) {
  return apiFetch('/notifications/read-all', { method: 'PATCH' }, token);
}

// ─── Founder Den / Public Founder Presence ───────────────────────────────────

export interface FounderVideo {
  id: string;
  type: string;
  title: string;
  url: string;
  durationMin: number | null;
  publishedAt: string;
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    presenceLevel: string;
  };
  project: {
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
  } | null;
}

export interface FounderBuildPost {
  id: string;
  dayNumber: number | null;
  headline: string;
  body: string;
  githubUrl: string | null;
  publishedAt: string;
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    presenceLevel: string;
  };
  project: {
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
  } | null;
}

export interface FounderRoom {
  slug: string;
  name: string;
  bio: string | null;
  photoUrl: string | null;
  linkedInUrl: string | null;
  twitterUrl: string | null;
  githubUrl: string | null;
  githubUsername: string | null;
  websiteUrl: string | null;
  journeyStage: string;
  buildStreakDays: number;
  reputationScore: number;
  presenceLevel: string;
  reputation: {
    total: number;
    videoActivity: number;
    githubActivity: number;
    communityTrust: number;
    productDelivery: number;
    consistency: number;
  };
  videos: FounderVideo[];
  buildPosts: FounderBuildPost[];
  projects: ProjectSummary[];
  heatmap: { date: string; count: number }[];
  stats: {
    videos: number;
    buildPosts: number;
    roadmapDone: number;
    githubConnected: boolean;
    buildStreakDays: number;
  };
}

export interface ProjectRoom {
  id: string;
  slug: string;
  name: string;
  ticker: string;
  summary: string | null;
  description: string | null;
  logoUrl: string | null;
  websiteUrl: string | null;
  dexscreenerUrl: string | null;
  chain: { slug: string; name: string };
  category: { slug: string; name: string } | null;
  lifecycleStage: string;
  listingKind?: 'verified' | 'founder_os' | 'paper_track';
  isVerifiedListing?: boolean;
  launchReadiness: number;
  plannedLaunchDate: string | null;
  launchRequestedAt: string | null;
  isLiveToken: boolean;
  launchPriceUsd: number | null;
  followerCount: number;
  isFollowing: boolean;
  founderScore: number;
  buildStreakDays: number;
  genome: {
    execution: number;
    demand: number;
    community: number;
    transparency: number;
    launchReady: number;
    overall: number;
  };
  lifecycleStages: { key: string; label: string; emoji: string }[];
  metrics: ProjectMetrics | null;
  socials: ProjectDetail['socials'];
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    presenceLevel: string;
    reputationScore: number;
    journeyStage: string;
    twitterUrl: string | null;
    githubUrl: string | null;
  } | null;
  videos: FounderVideo[];
  buildPosts: FounderBuildPost[];
  roadmap: { id: string; title: string; status: string; sortOrder: number }[];
  activeRaise: {
    id: string;
    goalUsd: number;
    tokenAllocation: string | null;
    communityTokenPercent?: number;
    maxParticipantSlots?: number | null;
    totalBurnedUsd?: number;
    slotsLocked?: boolean;
    durationDays: number;
    plannedLaunchDate: string | null;
    status: string;
    endsAt?: string | null;
    totalAllocated: number;
    allocatorCount: number;
    convictionScore: number;
    momentumScore?: number;
    allocationFeePercent?: number;
  } | null;
  allocationLeaderboard?: RaiseAllocationLeaderboardEntry[];
  demandAnalytics: {
    interestedUsers: number;
    averageCommitment: number;
    largestCommitment: number;
    demandRank: number | null;
    totalDemand: number;
  };
  demandPolls: {
    id: string;
    type: string;
    question: string;
    options: unknown;
    voteCounts: Record<string, number>;
  }[];
  communityChannels: string[];
  communityThreads: {
    id: string;
    channel: string;
    title: string;
    body: string;
    pinned: boolean;
    createdAt: string;
    commentCount: number;
    comments?: {
      id: string;
      userId: string;
      body: string;
      createdAt: string;
      isHelpful: boolean;
    }[];
  }[];
  isProjectFounder?: boolean;
  communityRewardPool?: number;
  openBounties?: {
    id: string;
    title: string;
    description: string;
    rewardCredits: number;
    rewardPoints: number;
  }[];
  launchpadAccess: {
    unlocked: boolean;
    launchReadiness: number;
    checks: Record<string, boolean>;
  };
}

export interface FounderDashboard {
  progressTier: string;
  founderScore: number;
  currentStage: string;
  followers: number;
  buildStreakDays: number;
  simulatedDemand: number;
  launchReadiness: number;
  cashBalance: number;
  hasFounderProfile: boolean;
  primaryProjectSlug: string | null;
  founderSlug: string | null;
  founderCredits: number;
  communityRewardPool: number;
  applicationPending: number;
}

export interface DiscoverProject {
  slug: string;
  name: string;
  ticker: string;
  summary: string | null;
  logoUrl: string | null;
  lifecycleStage: string;
  stageBucket: string;
  journeyProgress: number;
  launchReadiness: number;
  bubbleScore: number;
  followerCount: number;
  founderScore: number;
  buildStreakDays: number;
  simulatedDemand: number;
  raiseGoalUsd: number;
  demandPct: number;
  marketCap: number | null;
  priceUsd: number | null;
  volume24h: number | null;
  isLiveToken: boolean;
  founderVideoUrl: string | null;
  founderVideoTitle: string | null;
  lastUpdateAt: string;
  lastUpdateHeadline: string | null;
  category: { slug: string; name: string } | null;
  chain: { slug: string; name: string };
  founder: {
    slug: string;
    name: string;
    photoUrl: string | null;
    reputationScore: number;
    buildStreakDays: number;
  } | null;
}

export interface EcosystemPulse {
  recentActivity: FounderBuildPost[];
  trendingProjects: DiscoverProject[];
  topRaises: {
    project: { slug: string; name: string; ticker: string; logoUrl: string | null };
    goalUsd: number;
    totalDemand: number;
    allocatorCount: number;
  }[];
  liveTokenCount: number;
  buildingCount: number;
  ideaCount: number;
}

export function fetchLatestFounderVideos(limit = 12) {
  return apiFetch<FounderVideo[]>(`/founder-den/videos/latest?limit=${limit}`);
}

export function fetchBuildFeed(limit = 40) {
  return apiFetch<FounderBuildPost[]>(`/founder-den/build-feed?limit=${limit}`);
}

export function fetchFounderRoom(slug: string) {
  return apiFetch<FounderRoom>(`/founder-den/founders/${slug}`);
}

export function fetchProjectRoom(slug: string, token?: string) {
  return apiFetch<ProjectRoom>(`/founder-den/projects/${slug}/room`, undefined, token);
}

export function fetchFounderDashboard(token: string) {
  return apiFetch<FounderDashboard>('/founder-den/dashboard', undefined, token);
}

export function fetchDiscoverProjects(filter?: string, stageBucket?: string) {
  const qs = new URLSearchParams();
  if (filter) qs.set('filter', filter);
  if (stageBucket) qs.set('stageBucket', stageBucket);
  const q = qs.toString();
  return apiFetch<DiscoverProject[]>(`/founder-den/discover${q ? `?${q}` : ''}`);
}

export function fetchEcosystemPulse() {
  return apiFetch<EcosystemPulse>('/founder-den/ecosystem/pulse');
}

export function fetchEconomyStats() {
  return apiFetch<{
    cashInCirculation: number;
    allocatedToRaises: number;
    totalVirtualSupply: number;
    topUpFeeUsd: number;
    restrictedThresholdUsd: number;
  }>('/founder-den/economy/stats');
}

export function submitFounderApplication(
  data: {
    projectName: string;
    websiteUrl?: string;
    twitterHandle?: string;
    githubUrl?: string;
    videoUrl?: string;
    ideaDescription: string;
    lifecycleStage: string;
  },
  token: string,
) {
  return apiFetch<{ founderSlug: string; projectSlug: string }>(
    '/founder-den/founder-application',
    { method: 'POST', body: JSON.stringify(data) },
    token,
  );
}

export function createSimulatedRaise(
  data: {
    projectId: string;
    goalUsd: number;
    durationDays: number;
    tokenAllocation?: string;
    plannedLaunchDate?: string;
    communityTokenPercent?: number;
    maxParticipantSlots?: number;
  },
  token: string,
) {
  return apiFetch('/founder-den/simulated-raises', { method: 'POST', body: JSON.stringify(data) }, token);
}

export interface RaiseAllocationLeaderboardEntry {
  rank: number;
  userId: string;
  displayName: string;
  amountUsd: number;
  burnedUsd: number;
  walletAddress: string | null;
  slotReserved: boolean;
}

export interface PlatformEconomy {
  raiseAllocationFeePercent: number;
  tokenLaunchFeePercent: number;
  weeklyStipendUsd: number;
  rechargeFeeUsd: number;
  restrictedCashThresholdUsd: number;
  totalPaperBurned: number;
  treasury: { solana: string | null; evm: string | null };
  paperDollarSinks: string[];
}

export function fetchPlatformEconomy() {
  return apiFetch<PlatformEconomy>('/founder-den/platform/economy');
}

export function updatePlatformTreasury(
  body: { solanaTreasuryAddress?: string; evmTreasuryAddress?: string },
  token: string,
) {
  return apiFetch<{ success: boolean; treasury: { solanaTreasuryAddress: string | null; evmTreasuryAddress: string | null } }>(
    '/founder-den/platform/treasury',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  );
}

export interface TopUpPaymentRecord {
  id: string;
  reference: string;
  userId: string;
  userEmail: string;
  userName: string | null;
  asset: string;
  amountUsd: number;
  treasuryAddress: string;
  payerAddress: string | null;
  txSignature: string | null;
  status: string;
  confirmedAt: string | null;
  createdAt: string;
}

export function fetchTopUpPayments(token: string, limit = 50) {
  return apiFetch<TopUpPaymentRecord[]>(`/founder-den/platform/top-ups?limit=${limit}`, undefined, token);
}

export function exportRaiseParticipants(raiseId: string, token: string) {
  return apiFetch<{
    projectName: string;
    participantCount: number;
    communityTokenPercent: number;
    csv: string;
    participants: {
      displayName: string;
      walletAddress: string | null;
      amountUsd: number;
      allocationSharePercent: number;
    }[];
  }>(`/founder-den/raises/${raiseId}/export`, undefined, token);
}

export function lockRaiseSlots(raiseId: string, token: string) {
  return apiFetch<{ success: boolean; message: string }>(
    `/founder-den/raises/${raiseId}/lock-slots`,
    { method: 'POST' },
    token,
  );
}

export function followProject(projectId: string, token: string) {
  return apiFetch(`/founder-den/projects/${projectId}/follow`, { method: 'POST' }, token);
}

export function unfollowProject(projectId: string, token: string) {
  return apiFetch(`/founder-den/projects/${projectId}/unfollow`, { method: 'POST' }, token);
}

export function fetchDemandHeatmap() {
  return apiFetch<
    { project: { slug: string; name: string; ticker: string; logoUrl: string | null }; goalUsd: number; totalDemand: number; allocatorCount: number }[]
  >('/founder-den/demand-heatmap');
}

export function createBuildPost(
  data: { headline: string; body: string; projectId?: string; dayNumber?: number; githubUrl?: string },
  token: string,
) {
  return apiFetch<{ buildStreakDays?: number }>(
    '/founder-den/build-posts',
    { method: 'POST', body: JSON.stringify(data) },
    token,
  );
}

export interface XConnectionStatus {
  connected: boolean;
  canPostInstantly: boolean;
  tokenExpired?: boolean;
  twitterHandle: string | null;
  message: string;
}

export function fetchXConnectionStatus(token: string) {
  return apiFetch<XConnectionStatus>('/conviction-share/x-status', undefined, token);
}

export function postProofOfConvictionToX(
  input: { projectId: string; text: string; pnlPercent: number },
  token: string,
) {
  return apiFetch<{ ok: true; tweetId: string; tweetUrl: string }>(
    '/conviction-share/post-to-x',
    { method: 'POST', body: JSON.stringify(input) },
    token,
  );
}

export function enrichHotBuyShare(
  input: {
    projectSlug: string;
    buyerNames?: string[];
    pctOfActive?: number;
    detailLine?: string;
  },
  token: string,
) {
  return apiFetch<{ text: string; source: 'template' | 'ai' }>(
    '/feed/enrich-hot-buy-share',
    { method: 'POST', body: JSON.stringify(input) },
    token,
  );
}

export interface EngagementStats {
  activeContributors24h?: number;
  activeUsers24h?: number;
  topTierSize?: number;
  expectedWinnersToday: number;
  prizeRangeUsd: { min: number; max: number };
  winnerRatePercent: number;
  model?: string;
  latestDraw: {
    drawDate: string | null;
    activeUsers: number;
    winnerCount: number;
    totalPaidUsd: number;
    winners: { displayName: string; amountUsd: number; activityScore: number }[];
  };
}

export interface FounderOsDashboard {
  founderCredits: number;
  communityRewardPool: number;
  primaryProject: { id: string; slug: string; name: string; communityRewardPool: number } | null;
  connectedApps: {
    provider: string;
    label: string;
    connected: boolean;
    reputationBoost: number;
    billTip?: string;
    accountName?: string | null;
    webhookUrl?: string | null;
  }[];
  integrationProviders?: IntegrationProviderConfig[];
  pendingSuggestions: {
    id: string;
    headline: string;
    body: string;
    devSummary: string;
    traderSummary: string;
    source?: string;
    createdAt: string;
  }[];
  openBounties: { id: string; title: string; description: string; rewardCredits: number; rewardPoints: number }[];
  recentBuildSessions?: { id: string; title: string; creditsSpent: number; createdAt: string }[];
}

export interface IntegrationProviderConfig {
  key: string;
  label: string;
  connectType: 'repo' | 'oauth' | 'token' | 'toggle';
  reputationBoost: number;
  billTip: string;
  fields: { key: string; label: string; placeholder: string; required: boolean; secret?: boolean }[];
}

export type PublishDestinationsInput = { buildFeed?: boolean; x?: boolean; community?: boolean };

export function fetchFounderOsDashboard(token: string) {
  return apiFetch<FounderOsDashboard>('/founder-os/dashboard', undefined, token);
}

export function fetchIntegrationProviders() {
  return apiFetch<IntegrationProviderConfig[]>('/founder-os/integrations');
}

export function connectIntegration(
  data: { provider: string; token?: string; projectName?: string },
  token: string,
) {
  return apiFetch<{ success: boolean; accountName: string; webhookUrl?: string }>(
    '/founder-os/integrations/connect',
    { method: 'POST', body: JSON.stringify(data) },
    token,
  );
}

export function disconnectIntegration(provider: string, token: string) {
  return apiFetch<{ success: boolean }>(
    `/founder-os/integrations/${provider}/disconnect`,
    { method: 'POST' },
    token,
  );
}

export function runCursorBuildRoom(
  data: { title: string; prompt: string },
  token: string,
) {
  return apiFetch<{
    sessionId: string;
    creditsSpent: number;
    suggestion: { id: string; headline: string; body: string; devSummary: string; traderSummary: string };
  }>('/founder-os/build-room', { method: 'POST', body: JSON.stringify(data) }, token);
}

export function connectGitHubRepo(repoFullName: string, token: string) {
  return apiFetch<{ success: boolean; repoFullName: string }>(
    '/founder-os/github/connect',
    { method: 'POST', body: JSON.stringify({ repoFullName }) },
    token,
  );
}

export function syncGitHubCommits(token: string) {
  return apiFetch<{
    commits: { sha: string; message: string; date: string }[];
    suggestion: { id: string; headline: string; body: string; devSummary: string; traderSummary: string };
  }>('/founder-os/github/sync', { method: 'POST' }, token);
}

export function syncFounderOsMemory(token: string) {
  return apiFetch<{ synced: boolean; repo?: string; reason?: string }>(
    '/founder-os/memory/sync',
    { method: 'POST' },
    token,
  );
}

export function publishSuggestedUpdate(
  suggestionId: string,
  token: string,
  destinations?: PublishDestinationsInput,
) {
  return apiFetch<{
    success: boolean;
    buildPostId?: string;
    communityThreadId?: string;
    xTweetUrl?: string;
    destinations: Record<string, { ok: boolean; error?: string; skipped?: boolean; tweetUrl?: string }>;
  }>(
    `/founder-os/suggestions/${suggestionId}/publish`,
    { method: 'POST', body: JSON.stringify(destinations ?? {}) },
    token,
  );
}

export function dismissSuggestedUpdate(suggestionId: string, token: string) {
  return apiFetch<{ success: boolean }>(
    `/founder-os/suggestions/${suggestionId}/dismiss`,
    { method: 'POST' },
    token,
  );
}

export function markCommentHelpful(projectId: string, commentId: string, token: string) {
  return apiFetch<{ success: boolean; pointsAwarded: number }>(
    `/founder-os/projects/${projectId}/comments/${commentId}/helpful`,
    { method: 'POST' },
    token,
  );
}

export function createFounderBounty(
  projectId: string,
  data: { title: string; description: string; rewardCredits: number; rewardPoints?: number },
  token: string,
) {
  return apiFetch(`/founder-os/projects/${projectId}/bounties`, {
    method: 'POST',
    body: JSON.stringify(data),
  }, token);
}

export function postCommunityComment(
  threadId: string,
  body: string,
  token: string,
) {
  return apiFetch(`/founder-den/threads/${threadId}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  }, token);
}

export function fetchEngagementStats() {
  return apiFetch<EngagementStats>('/engagement-rewards/stats');
}

export function allocateToRaise(raiseId: string, amountUsd: number, token: string) {
  return apiFetch(
    `/founder-den/raises/${raiseId}/allocate`,
    { method: 'POST', body: JSON.stringify({ amountUsd }) },
    token,
  );
}

export function voteDemandPoll(pollId: string, optionKey: string, token: string) {
  return apiFetch(
    `/founder-den/polls/${pollId}/vote`,
    { method: 'POST', body: JSON.stringify({ optionKey }) },
    token,
  );
}

// ─── Security ────────────────────────────────────────────────────────────────

export interface SecurityProfile {
  email: string;
  hasPassword: boolean;
  totpEnabled: boolean;
  totpPendingSetup: boolean;
  passkeys: {
    id: string;
    credentialId: string;
    label: string | null;
    deviceType: string | null;
    backedUp: boolean;
    createdAt: string;
    lastUsedAt: string | null;
  }[];
  recoveryCodesRemaining: number;
  /** @deprecated use solanaWallet */
  wallet: { chain: string; address: string; verifiedAt: string } | null;
  solanaWallet: { chain: string; address: string; verifiedAt: string } | null;
  evmWallet: { chain: string; address: string; verifiedAt: string } | null;
  securityScore: SecurityScoreResult;
}

export function verify2FaLogin(pendingToken: string, totpCode?: string, recoveryCode?: string) {
  return apiFetch<LoginApiResponse>('/auth/verify-2fa', {
    method: 'POST',
    body: JSON.stringify({ pendingToken, totpCode, recoveryCode }),
  });
}

export function fetchSecurityProfile(token: string) {
  return apiFetch<SecurityProfile>('/security/profile', {}, token);
}

export function changePassword(currentPassword: string, newPassword: string, token: string) {
  return apiFetch('/security/password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  }, token);
}

export function setupTotp(token: string) {
  return apiFetch<{ secret: string; otpauthUrl: string }>('/security/totp/setup', { method: 'POST' }, token);
}

export function enableTotp(code: string, token: string) {
  return apiFetch('/security/totp/enable', {
    method: 'POST',
    body: JSON.stringify({ code }),
  }, token);
}

export function disableTotp(code: string, token: string) {
  return apiFetch('/security/totp/disable', {
    method: 'POST',
    body: JSON.stringify({ code }),
  }, token);
}

export function generateRecoveryCodes(code: string | undefined, token: string) {
  return apiFetch<{ codes: string[] }>(
    '/security/recovery-codes',
    {
      method: 'POST',
      body: JSON.stringify(code ? { code } : {}),
    },
    token,
  );
}

export function passkeyRegisterOptions(token: string) {
  return apiFetch<{ options: PublicKeyCredentialCreationOptions; registerToken: string }>(
    '/security/passkey/register-options',
    { method: 'POST' },
    token,
  );
}

export function passkeyRegisterVerify(
  registerToken: string,
  response: Record<string, unknown>,
  token: string,
  label?: string,
) {
  return apiFetch<{ ok: boolean; recoveryCodes?: string[] }>(
    '/security/passkey/register-verify',
    {
      method: 'POST',
      body: JSON.stringify({ registerToken, response, label }),
    },
    token,
  );
}

export function passkeyLoginOptions(pendingToken: string) {
  return apiFetch<{ options: PublicKeyCredentialRequestOptions; passkeyToken: string }>(
    '/security/passkey/login-options',
    { method: 'POST', body: JSON.stringify({ pendingToken }) },
  );
}

export function passkeyLoginVerify(passkeyToken: string, response: Record<string, unknown>) {
  return apiFetch<LoginApiResponse>('/security/passkey/login-verify', {
    method: 'POST',
    body: JSON.stringify({ passkeyToken, response }),
  });
}

export function deletePasskey(credentialId: string, token: string) {
  return apiFetch(`/security/passkey/${encodeURIComponent(credentialId)}`, { method: 'DELETE' }, token);
}

export function walletChallenge(token: string) {
  return apiFetch<{ challengeToken: string; message: string }>(
    '/security/wallet/challenge',
    { method: 'POST' },
    token,
  );
}

export function walletVerify(
  challengeToken: string,
  address: string,
  signature: string,
  message: string,
  token: string,
  chain?: 'SOLANA' | 'ETHEREUM',
) {
  return apiFetch('/security/wallet/verify', {
    method: 'POST',
    body: JSON.stringify({ challengeToken, address, signature, message, chain }),
  }, token);
}

export function disconnectWallet(token: string, chain = 'SOLANA') {
  return apiFetch(`/security/wallet/${chain}`, { method: 'DELETE' }, token);
}

// ─── Account hub ─────────────────────────────────────────────────────────────

export interface AccountOverview {
  userId: string;
  username: string;
  email: string;
  avatarUrl: string | null;
  joinedAt: string;
  platformRole: string;
  gamifiedRole: GamifiedRole;
  isAdmin: boolean;
  adminBanner: string | null;
  authMethods: { provider: string; label: string; connected: boolean }[];
  reputation: ReputationMe;
  builderStatus: {
    isFounder: boolean;
    badge: string | null;
    presenceLevel: string | null;
    founderSlug: string | null;
  };
  followingCount: number;
  followersCount: number;
}

export interface AccountPointLedgerEntry {
  id: string;
  amount: number;
  actionKey: string;
  label: string;
  createdAt: string;
}

export interface AccountActivityItem {
  id: string;
  type: string;
  title: string;
  body: string;
  link: string | null;
  readAt: string | null;
  createdAt: string;
}

export interface AccountFollowingEntry {
  userId: string;
  displayName: string;
  twitterHandle: string | null;
  reputationPoints: number;
  contributorLevel: number;
  founderSlug: string | null;
  followedAt: string;
}

export function fetchAccountOverview(token: string) {
  return apiFetch<AccountOverview>('/account/overview', undefined, token);
}

export function fetchAccountPointLedger(token: string, limit = 50) {
  return apiFetch<AccountPointLedgerEntry[]>(`/account/point-ledger?limit=${limit}`, undefined, token);
}

export function fetchAccountActivity(token: string, limit = 40) {
  return apiFetch<AccountActivityItem[]>(`/account/activity?limit=${limit}`, undefined, token);
}

export function fetchNotificationPreferences(token: string) {
  return apiFetch<NotificationPreferenceGroups>('/account/notification-preferences', undefined, token);
}

export function updateNotificationPreferences(prefs: Partial<NotificationPreferenceGroups>, token: string) {
  return apiFetch<NotificationPreferenceGroups>(
    '/account/notification-preferences',
    { method: 'PUT', body: JSON.stringify(prefs) },
    token,
  );
}

export function fetchAccountFollowing(token: string) {
  return apiFetch<AccountFollowingEntry[]>('/account/following', undefined, token);
}

export function followUser(userId: string, token: string) {
  return apiFetch<{ following: boolean }>(`/account/follow/${userId}`, { method: 'POST' }, token);
}

export function unfollowUser(userId: string, token: string) {
  return apiFetch<{ following: boolean }>(`/account/follow/${userId}`, { method: 'DELETE' }, token);
}

// ─── Agents ──────────────────────────────────────────────────────────────────

export interface FounderAgentSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  category: string;
  template: string;
  isPublic: boolean;
  followerCount: number;
  usageCount: number;
  rating: number;
  ratingCount: number;
  revenueCredits: number;
  founder: { id: string; slug: string; name: string };
  project: { id: string; slug: string; name: string } | null;
  createdAt: string;
  installed?: boolean;
  following?: boolean;
}

export interface AgentHubResponse {
  agents: FounderAgentSummary[];
  templates: { key: string; label: string; category: string; description: string }[];
  categories: string[];
}

export function fetchAgentHub(category?: string) {
  const q = category ? `?category=${encodeURIComponent(category)}` : '';
  return apiFetch<AgentHubResponse>(`/agents${q}`);
}

export function fetchAgent(slug: string, token?: string) {
  return apiFetch<FounderAgentSummary>(`/agents/${slug}`, {}, token);
}

export function fetchMyAgents(token: string) {
  return apiFetch<{ created: FounderAgentSummary[]; installed: FounderAgentSummary[] }>(
    '/agents/my/list',
    {},
    token,
  );
}

export function createAgent(
  data: { name: string; description?: string; category: string; template?: string; projectId?: string },
  token: string,
) {
  return apiFetch<FounderAgentSummary>('/agents', { method: 'POST', body: JSON.stringify(data) }, token);
}

export function installAgent(agentId: string, token: string) {
  return apiFetch(`/agents/${agentId}/install`, { method: 'POST' }, token);
}

export function runAgent(agentId: string, prompt: string, token: string) {
  return apiFetch<{
    runId: string;
    creditsSpent: number;
    output: {
      title: string;
      summary: string;
      tasks: string[];
      githubIssues: string[];
      buildPlan: string[];
      traderView: string;
    };
  }>(`/agents/${agentId}/run`, { method: 'POST', body: JSON.stringify({ prompt }) }, token);
}

// ─── Build Queue (Phase 4) ───────────────────────────────────────────────────

export type BuildQueueItemKind = 'IDEA' | 'TASK' | 'GITHUB_ISSUE' | 'ROADMAP' | 'SPEC';
export type BuildQueueStatus =
  | 'CAPTURED'
  | 'SPECCED'
  | 'QUEUED'
  | 'IN_PROGRESS'
  | 'BLOCKED'
  | 'DONE'
  | 'DISMISSED';

export interface BuildQueueItem {
  id: string;
  kind: BuildQueueItemKind;
  status: BuildQueueStatus;
  source: string;
  title: string;
  description: string | null;
  spec: string | null;
  githubIssueTitle: string | null;
  githubIssueUrl: string | null;
  cursorPrompt: string | null;
  roadmapItemId: string | null;
  agentRunId: string | null;
  sortOrder: number;
  parentId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface BuildRoomData {
  repoFullName: string | null;
  cursorConnected: boolean;
  githubConnected: boolean;
  githubTokenConnected?: boolean;
  defaultAiProvider?: string;
  autoCreateGitHubIssues?: boolean;
  grouped: {
    ideas: BuildQueueItem[];
    tasks: BuildQueueItem[];
    issues: BuildQueueItem[];
    specs: BuildQueueItem[];
    roadmap: BuildQueueItem[];
  };
  commits: { sha: string; message: string; date: string }[];
  deployments: { id: string; headline: string; source: string; status: string; createdAt: string }[];
  pullRequests: { title: string; url: string; state: string; number?: number }[];
  pendingSuggestions: { id: string; headline: string; body: string; source: string; createdAt: string }[];
  cursorCopy: string;
  stats: { ideas: number; tasks: number; issues: number; commits: number };
}

export type CommandBarIntent = 'roadmap' | 'release_notes' | 'weekly_summary';

export function fetchBuildRoom(token: string) {
  return apiFetch<BuildRoomData>('/build-queue/room', undefined, token);
}

export function quickBuild(prompt: string, token: string, source?: 'QUICK_BUILD' | 'VOICE') {
  return apiFetch<{
    ideaId: string;
    cursorPrompt: string;
    cursorCopy: string;
    openHandsDispatch?: {
      conversationUrl?: string | null;
      status?: string;
      error?: string;
    } | null;
    cursorCloudDispatch?: {
      agentUrl?: string;
      agentId?: string;
      runId?: string;
      status?: string;
      mode?: 'create' | 'follow_up';
      error?: string;
    } | null;
    parsed: { ideaTitle: string; tasks: string[]; githubIssues: string[] };
  }>('/build-queue/quick-build', { method: 'POST', body: JSON.stringify({ prompt, source }) }, token);
}

export function runCommandBar(intent: CommandBarIntent, prompt: string | undefined, token: string) {
  return apiFetch<{
    creditsSpent: number;
    cursorCopy: string;
    result: { title: string; summary: string; body: string };
  }>('/build-queue/command', { method: 'POST', body: JSON.stringify({ intent, prompt }) }, token);
}

export function updateBuildQueueItem(
  id: string,
  data: { status?: BuildQueueStatus; title?: string },
  token: string,
) {
  return apiFetch<{ item: BuildQueueItem | null }>(
    `/build-queue/${id}`,
    { method: 'PATCH', body: JSON.stringify(data) },
    token,
  );
}

export function dismissBuildQueueItem(id: string, token: string) {
  return apiFetch(`/build-queue/${id}/dismiss`, { method: 'POST' }, token);
}

export function publishGitHubIssues(token: string) {
  return apiFetch<{ created: number; repoFullName: string }>(
    '/build-queue/publish-github-issues',
    { method: 'POST' },
    token,
  );
}

// ─── Builder settings (Phase 4B) ───────────────────────────────────────────────

export interface BuilderSettings {
  defaultProvider: string;
  preferredModel: string | null;
  autoCreateGitHubIssues: boolean;
  autoPublishOnEvent: boolean;
  currentGoalFocus: string | null;
  memoryStorageMode?: string;
  githubTokenConnected: boolean;
  openHandsBaseUrl: string | null;
  cursorAgentUrl: string | null;
  founderNodeAi?: {
    paired: boolean;
    online: boolean;
    ollamaReady: boolean;
    ollamaModel: string | null;
    nodeLabel: string | null;
    directOllamaUrl: string | null;
  };
  founderNodeV2?: {
    paired: boolean;
    online: boolean;
    nodeLabel: string | null;
    vectorChunks: number | null;
    vectorIndexedAt: string | null;
    lastPullSyncAt: string | null;
    pendingJobs: number;
    bidirectionalSync: boolean;
  };
  phalaPrivateAi?: {
    ready: boolean;
    userKeyConnected: boolean;
    platformAvailable: boolean;
    inferenceUrl: string;
    model: string;
    docsUrl: string;
  };
  providers: {
    key: string;
    label: string;
    needsApiKey: boolean;
    connectMode?: string;
    needsBaseUrl?: boolean;
    defaultModel: string | null;
    billTip: string;
    credentialProvider: string | null;
    connected: boolean;
  }[];
}

export function fetchBuilderSettings(token: string) {
  return apiFetch<BuilderSettings>('/builder/settings', undefined, token);
}

export function updateBuilderSettings(
  data: {
    defaultProvider?: string;
    preferredModel?: string;
    autoCreateGitHubIssues?: boolean;
    autoPublishOnEvent?: boolean;
    currentGoalFocus?: string;
    memoryStorageMode?: string;
  },
  token: string,
) {
  return apiFetch('/builder/settings', { method: 'PATCH', body: JSON.stringify(data) }, token);
}

export function searchFounderVault(query: string, token: string, topK = 5) {
  return apiFetch<{ ok?: boolean; query?: string; hits?: Array<{ source: string; text: string; score: number }> }>(
    '/founder-node/sync-jobs/vault-search',
    { method: 'POST', body: JSON.stringify({ query, topK }) },
    token,
  );
}

export function runFounderNodeAgent(
  agent: 'vault-index' | 'goal-align' | 'vault-summary',
  token: string,
  payload?: { goal?: string; query?: string },
) {
  return apiFetch<Record<string, unknown>>(
    '/founder-node/sync-jobs/run-agent',
    { method: 'POST', body: JSON.stringify({ agent, ...payload }) },
    token,
  );
}

export function pushGoalToFounderNode(goal: string, token: string) {
  return apiFetch('/founder-node/sync-jobs/push-goal', {
    method: 'POST',
    body: JSON.stringify({ goal }),
  }, token);
}

export function fetchAttestationDashboard(token: string) {
  return apiFetch<{
    memoryIntegrity: {
      mode: string;
      score: number;
      status: 'healthy' | 'partial' | 'offline';
      checks: Array<{ name: string; ok: boolean; detail?: string }>;
      lastVaultScanAt: string | null;
    };
    phalaTee: {
      recentCount: number;
      verifiedCount: number;
      latest: {
        id: string;
        model: string | null;
        requestId: string | null;
        signingAddress: string | null;
        verified: boolean;
        status: string;
        createdAt: string;
        summary: string | null;
      } | null;
      docsUrl: string;
    };
    recent: Array<{
      id: string;
      kind: string;
      model: string | null;
      requestId: string | null;
      verified: boolean;
      status: string;
      summary: string | null;
      createdAt: string;
    }>;
  }>('/attestation/dashboard', undefined, token);
}

export function scanVaultIntegrity(token: string) {
  return apiFetch('/attestation/vault-scan', { method: 'POST' }, token);
}

export function verifyPhalaAttestation(token: string, logId?: string) {
  return apiFetch<{
    verified: boolean;
    summary: string | null;
    checks?: Array<{ name: string; ok: boolean; detail?: string }>;
  }>('/attestation/phala/verify', { method: 'POST', body: JSON.stringify({ logId }) }, token);
}

export function connectAiProvider(provider: string, apiKey: string, token: string) {
  return apiFetch<{ success: boolean; accountName: string }>(
    '/builder/providers/connect',
    { method: 'POST', body: JSON.stringify({ provider, apiKey }) },
    token,
  );
}

export function connectOllamaDirect(baseUrl: string, model: string | undefined, token: string) {
  return apiFetch<{ success: boolean; accountName: string; baseUrl: string }>(
    '/builder/providers/ollama-connect',
    { method: 'POST', body: JSON.stringify({ baseUrl, model }) },
    token,
  );
}

export function connectPhalaDirect(
  apiKey: string,
  inferenceUrl: string | undefined,
  model: string | undefined,
  token: string,
) {
  return apiFetch<{
    success: boolean;
    accountName: string;
    inferenceUrl: string;
    model: string;
  }>(
    '/builder/providers/phala-connect',
    { method: 'POST', body: JSON.stringify({ apiKey, inferenceUrl, model }) },
    token,
  );
}

export function connectOpenHands(baseUrl: string, apiKey: string, token: string) {
  return apiFetch<{
    success: boolean;
    accountName: string;
    baseUrl: string;
    apiVersion: string;
  }>(
    '/builder/providers/openhands-connect',
    { method: 'POST', body: JSON.stringify({ baseUrl, apiKey }) },
    token,
  );
}

export function connectCursorCloud(apiKey: string, token: string) {
  return apiFetch<{
    success: boolean;
    accountName: string;
    agentUrl: string | null;
  }>(
    '/builder/providers/cursor-connect',
    { method: 'POST', body: JSON.stringify({ apiKey }) },
    token,
  );
}

export function dispatchOpenHandsBuild(
  data: { spec: string; cursorPrompt?: string; repository?: string },
  token: string,
) {
  return apiFetch<{
    apiVersion: string;
    startTaskId: string;
    conversationId: string | null;
    status: string;
    conversationUrl: string | null;
  }>('/builder/openhands/dispatch', { method: 'POST', body: JSON.stringify(data) }, token);
}

export function dispatchCursorCloudBuild(
  data: { spec: string; cursorPrompt?: string; repository?: string },
  token: string,
) {
  return apiFetch<{
    agentId: string;
    runId: string;
    status: string;
    agentUrl: string;
    mode: 'create' | 'follow_up';
  }>('/builder/cursor/dispatch', { method: 'POST', body: JSON.stringify(data) }, token);
}

export function disconnectAiProvider(provider: string, token: string) {
  return apiFetch(`/builder/providers/${provider}/disconnect`, { method: 'POST' }, token);
}

export function connectGitHubToken(githubToken: string, token: string) {
  return apiFetch<{ success: boolean; githubUsername: string }>(
    '/builder/github-token',
    { method: 'POST', body: JSON.stringify({ token: githubToken }) },
    token,
  );
}

export function disconnectGitHubToken(token: string) {
  return apiFetch('/builder/github-token', { method: 'DELETE' }, token);
}

// ─── Event Bus & Copilot (Phase 5) ───────────────────────────────────────────

export interface FounderEventItem {
  id: string;
  type: string;
  source: string;
  title: string;
  payload: unknown;
  status: string;
  createdAt: string;
}

export interface EventActivityFeed {
  projectName: string;
  launchReadiness: number;
  buildStreakDays: number;
  weekStats: {
    commits: number;
    deploys: number;
    followers: number;
    featureRequests: number;
    events: number;
  };
  recentEvents: { id: string; type: string; source: string; title: string; createdAt: string }[];
}

export function fetchEvents(token: string) {
  return apiFetch<{ events: FounderEventItem[] }>('/events', undefined, token);
}

export function fetchEventActivity(token: string) {
  return apiFetch<EventActivityFeed>('/events/activity', undefined, token);
}

export function copilotAsk(prompt: string, token: string) {
  return apiFetch<{
    answer: string;
    answerProvider?: string;
    llmErrors?: string[];
    stats: Record<string, number>;
  }>('/copilot/ask', { method: 'POST', body: JSON.stringify({ prompt }) }, token);
}

export function copilotHandsFree(prompt: string, token: string) {
  return apiFetch<{ action: string; answer: string; cursorCopy?: string }>(
    '/copilot/hands-free',
    { method: 'POST', body: JSON.stringify({ prompt }) },
    token,
  );
}

export interface ProjectMemory {
  welcomeMessage: string;
  project: { id: string; name: string; slug: string; lifecycleStage: string } | null;
  currentGoal: string;
  progressPercent: number;
  launchReadiness: number;
  buildStreakDays: number;
  lastActivityAt: string | null;
  lastActivityLabel: string;
  lastCommit: string | null;
  repoFullName: string | null;
  currentBranch: string | null;
  openTasks: { id: string; title: string; kind: string; status: string; done: boolean }[];
  suggestedNextStep: string;
  deployments: { provider: string; label: string; healthy: boolean }[];
  raiseStatus: {
    goalUsd: number;
    allocatedUsd: number;
    participantCount: number;
    status: string;
  } | null;
  community: { followers: number; featureRequests: number };
  defaultAiProvider: string;
  memoryStorageMode?: string;
  cursorCopy: string;
  githubMemory?: {
    repoFullName: string;
    hasProjectContext: boolean;
    hasRoadmap: boolean;
    openTasksFromRepo: { id: string; title: string; status: string; kind: string; done: boolean }[];
  } | null;
  deviceSync?: {
    updatedAt: string;
    deviceLabel: string | null;
    payload: import('@dcf/utils').DeviceMemoryPayload;
  } | null;
  vaultRelay?: import('@dcf/utils').VaultRelaySummary | null;
  connectedNodes?: Array<{
    nodeId: string;
    label: string;
    status: 'online' | 'offline';
    lastSeenAt: string | null;
    ramGb: number | null;
    storageGb: number | null;
    storageFreeGb: number | null;
    vaultHealthy: boolean;
    platform: string | null;
  }>;
}

export function fetchDeviceMemorySync(token: string) {
  return apiFetch<{
    updatedAt: string | null;
    deviceLabel: string | null;
    payload: import('@dcf/utils').DeviceMemoryPayload | null;
  }>('/copilot/memory/device-sync', undefined, token);
}

export function pushDeviceMemorySync(
  payload: import('@dcf/utils').DeviceMemoryPayload,
  token: string,
) {
  return apiFetch<{ success: boolean; updatedAt: string }>(
    '/copilot/memory/device-sync',
    { method: 'POST', body: JSON.stringify(payload) },
    token,
  );
}

export interface FounderNodeStatusRow {
  id: string;
  nodeId: string;
  label: string;
  status: 'online' | 'offline';
  lastSeenAt: string | null;
  ramGb: number | null;
  storageGb: number | null;
  storageFreeGb: number | null;
  vaultHealthy: boolean;
  platform: string | null;
  appVersion: string | null;
}

export function createFounderNodePairingCode(token: string) {
  return apiFetch<{ code: string; expiresAt: string }>(
    '/founder-node/pairing-code',
    { method: 'POST' },
    token,
  );
}

export function fetchFounderNodeStatus(token: string) {
  return apiFetch<{ nodes: FounderNodeStatusRow[] }>('/founder-node/status', undefined, token);
}

export function revokeFounderNode(nodeId: string, token: string) {
  return apiFetch<{ success: boolean }>(`/founder-node/${nodeId}`, { method: 'DELETE' }, token);
}

export function fetchCopilotMemory(token: string) {
  return apiFetch<ProjectMemory>('/copilot/memory', undefined, token);
}

export function fetchCopilotStandup(token: string) {
  return apiFetch<{ standup: string; memory: ProjectMemory }>('/copilot/standup', undefined, token);
}

export function copilotResume(token: string) {
  return apiFetch<{
    message: string;
    memory: ProjectMemory;
    cursorCopy: string;
    dispatchHint?: string;
    cursorCloudDispatch?: {
      agentUrl?: string;
      agentId?: string;
      runId?: string;
      status?: string;
      mode?: 'create' | 'follow_up';
      error?: string;
    } | null;
    openHandsDispatch?: {
      conversationUrl?: string | null;
      status?: string;
      error?: string;
    } | null;
  }>('/copilot/resume', { method: 'POST' }, token);
}

// ─── Scout Markets & Founder Brain (Phase 7) ─────────────────────────────────

export interface ScoutMarketItem {
  id: string;
  question: string;
  status: string;
  source?: string;
  yesPoolUsd: number;
  noPoolUsd: number;
  totalPoolUsd?: number;
  conviction: number;
  participantCount: number;
  resolvesAt?: string | null;
  hoursLeft?: number | null;
  outcome?: boolean | null;
  project: { slug: string; name: string; ticker: string; logoUrl: string | null };
  creatorName?: string | null;
  viewerPosition: { side: string; amountUsd: number } | null;
  heatScore?: number;
  heatLabel?: 'Blazing' | 'Heating up' | null;
}

export type PredictionMarketItem = ScoutMarketItem;

export function fetchPredictionMarkets(token?: string) {
  return apiFetch<PredictionMarketItem[]>('/prediction-markets', undefined, token);
}

export function fetchScoutMarkets(slug: string, token?: string) {
  return apiFetch<ScoutMarketItem[]>(`/founder-den/projects/${slug}/scout-markets`, undefined, token);
}

export function createPredictionMarket(
  body: { projectSlug: string; question: string },
  token: string,
) {
  return apiFetch<PredictionMarketItem>(
    '/prediction-markets',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  );
}

export function stakePredictionMarket(
  marketId: string,
  side: 'YES' | 'NO',
  amountUsd: number,
  token: string,
) {
  return apiFetch<{
    success: boolean;
    conviction: number;
    yesPoolUsd: number;
    noPoolUsd: number;
    totalPoolUsd: number;
    heatLabel?: 'Blazing' | 'Heating up' | null;
  }>(
    `/prediction-markets/${marketId}/stake`,
    { method: 'POST', body: JSON.stringify({ side, amountUsd }) },
    token,
  );
}

export function stakeScoutMarket(marketId: string, side: 'YES' | 'NO', amountUsd: number, token: string) {
  return stakePredictionMarket(marketId, side, amountUsd, token);
}

export function askFounderBrain(slug: string, question: string) {
  return apiFetch<{ question: string; answer: string; source: string; starterQuestions: string[] }>(
    `/founder-den/projects/${slug}/brain/ask`,
    { method: 'POST', body: JSON.stringify({ question }) },
  );
}

import { apiUrl, describeApiTarget } from './api-base';

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
  }[];
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

export function loginAccount(input: { email: string; password: string }) {
  return apiFetch<{ accessToken: string; user: { id: string; email: string; role: string } }>(
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
}) {
  return apiFetch<{
    success: boolean;
    feedPostId: string;
    ticker: string;
    amountUsd: number;
  }>('/paper-trading/trade', {
    method: 'POST',
    body: JSON.stringify(input),
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
    stripeEnabled: boolean;
    message: string;
  }>('/paper-trading/reset-info');
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
}

export interface ProjectDetail extends ProjectSummary {
  description: string | null;
  docsUrl: string | null;
  whitepaperUrl: string | null;
  contractAddress: string | null;
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
  readAt: string | null;
  createdAt: string;
}

export function fetchSpotlightProjects() {
  return apiFetch<SpotlightProject[]>('/founder-updates/spotlight');
}

export function fetchPinnedFounderUpdates() {
  return apiFetch<FounderUpdate[]>('/founder-updates/pinned');
}

export function fetchNotifications(token: string) {
  return apiFetch<AppNotification[]>('/notifications', undefined, token);
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
    durationDays: number;
    plannedLaunchDate: string | null;
    status: string;
    totalAllocated: number;
    allocatorCount: number;
    convictionScore: number;
  } | null;
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
  applicationPending: number;
}

export interface DiscoverProject {
  slug: string;
  name: string;
  ticker: string;
  summary: string | null;
  logoUrl: string | null;
  lifecycleStage: string;
  launchReadiness: number;
  bubbleScore: number;
  followerCount: number;
  founderScore: number;
  buildStreakDays: number;
  simulatedDemand: number;
  isLiveToken: boolean;
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

export function fetchDiscoverProjects(filter?: string) {
  const q = filter ? `?filter=${encodeURIComponent(filter)}` : '';
  return apiFetch<DiscoverProject[]>(`/founder-den/discover${q}`);
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
  },
  token: string,
) {
  return apiFetch('/founder-den/simulated-raises', { method: 'POST', body: JSON.stringify(data) }, token);
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

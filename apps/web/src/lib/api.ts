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
  marketPreview?: DexScreenerPreview['marketPreview'];
}

export interface PendingApplication {
  id: string;
  projectName: string;
  ticker: string;
  chainSlug: string | null;
  websiteUrl: string | null;
  founderName: string | null;
  founderVideoUrl: string | null;
  founderInterviewUrl: string | null;
  founderLinkedIn: string | null;
  founderGithub: string | null;
  companyDetails: string | null;
  dexscreenerUrl: string | null;
  logoUrl: string | null;
  verificationScore: number;
  verificationCriteria: string[] | null;
  status: string;
  createdAt: string;
}

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
    chainSlug?: string;
    quantity: number;
    avgBuyPrice: number;
    priceUsd: number;
    marketValue: number;
    pnl: number;
    pnlPercent: number;
  }[];
}

export interface LeaderboardEntry {
  rank: number;
  userId: string;
  displayName: string;
  totalValue: number;
  pnl: number;
  roi: number;
  period: string;
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

export function submitListingApplication(data: ListingFormData) {
  return apiFetch<{ id: string; status: string; projectName: string; verificationScore: number }>(
    '/listing-applications',
    { method: 'POST', body: JSON.stringify(data) },
  );
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
  reviewNotes?: string,
) {
  return apiFetch<ReviewListingResult>(
    `/listing-applications/${id}/review`,
    {
      method: 'PATCH',
      body: JSON.stringify({ status, reviewNotes }),
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
  cashBalance: number;
  totalValue: number;
  pnl: number;
  roi: number;
  startingCash: number;
  positionCount: number;
  positions: {
    ticker: string;
    name: string;
    logoUrl: string | null;
    marketValue: number;
    pnl: number;
    pnlPercent: number;
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

/**
 * Railway website backend + Neon are being removed (Danish via RK t781u).
 * Fly showcase runtime is already deleted. Vercel keeps a static / marketing site.
 *
 * Default ON so production never opens broken fetch/error loops to Railway `/api/*`.
 * Set NEXT_PUBLIC_BACKEND_API_RETIRED=0 only for local full-stack / unit-test stubs.
 */

export const BACKEND_API_RETIRED_REASON =
  'Website API and Neon database are retired. Railway backend is being removed (RK t781u). Fly showcase already deleted.';

export const BACKEND_API_GONE_CODE = 'BACKEND_API_RETIRED' as const;

export class BackendApiRetiredError extends Error {
  readonly code = BACKEND_API_GONE_CODE;

  constructor(message = BACKEND_API_RETIRED_REASON) {
    super(message);
    this.name = 'BackendApiRetiredError';
  }
}

/** True when Nest/Railway `/api` must not be contacted. */
export function isBackendApiRetired(): boolean {
  const raw = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED?.trim().toLowerCase();
  if (raw === '0' || raw === 'false' || raw === 'off' || raw === 'no') {
    return false;
  }
  // Default retired — fail closed once Railway/Neon leave.
  return true;
}

export function throwIfBackendApiRetired(): void {
  if (isBackendApiRetired()) {
    throw new BackendApiRetiredError();
  }
}

export function backendApiGonePayload() {
  return {
    ok: false as const,
    code: BACKEND_API_GONE_CODE,
    message: BACKEND_API_RETIRED_REASON,
    cite: 't781u',
    fly: 'deleted',
    railwayNeon: 'removal-planned',
    r50c: 'static-site-reduces-api-dependency',
  };
}

/** Paths that stay useful as static marketing / legal without the Nest API. */
export const STATIC_SITE_ALLOWLIST_PREFIXES = [
  '/',
  '/privacy',
  '/rules',
  '/legal',
  '/pricing',
  '/mobile',
  '/platform-offline',
  '/busted',
] as const;

/** App routes that depended on Railway `/api` — replaced with static offline UX. */
export const API_DEPENDENT_ROUTE_PREFIXES = [
  '/login',
  '/register',
  '/account',
  '/paper-trading',
  '/agent-hub',
  '/agents',
  '/portfolio',
  '/leaderboard',
  '/watchlist',
  '/notifications',
  '/chat',
  '/founder-chat',
  '/docs/signal-api',
  '/admin',
  '/feed',
  '/build-feed',
  '/town-hall',
  '/ddollar',
  '/predict',
  '/discover',
  '/projects',
  '/project',
  '/founders',
  '/founder',
  '/founder-ide',
  '/founder-node',
  '/founder-economics',
  '/list-your-project',
  '/raise-room',
  '/trust-center',
  '/scout-votes',
  '/reputation',
  '/settings',
  '/airdrop',
  '/builder-rewards',
] as const;

export function isApiDependentPath(pathname: string): boolean {
  const path = pathname.split('?')[0] || '/';
  if (path === '/platform-offline') return false;
  return API_DEPENDENT_ROUTE_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
}

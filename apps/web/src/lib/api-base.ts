/** Client API base. Empty = same-origin `/api` (proxied to Nest via next.config rewrites). */
export function getPublicApiBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (raw === '') return '';
  if (raw?.trim()) return raw.trim().replace(/\/$/, '');
  if (process.env.NODE_ENV === 'development') return '';
  // Production default: same-origin /api (Vercel rewrites to API_URL — no CORS).
  return '';
}

/** Server-side API base (NextAuth, route handlers). Always hits local Nest. */
export function getServerApiBase(): string {
  const raw = process.env.API_URL?.trim();
  if (raw) return raw.replace(/\/$/, '');
  return 'http://127.0.0.1:4000';
}

export function apiUrl(path: string, forServer = false): string {
  const base = forServer ? getServerApiBase() : getPublicApiBase();
  const suffix = path.startsWith('/api')
    ? path
    : `/api${path.startsWith('/') ? path : `/${path}`}`;
  return base ? `${base}${suffix}` : suffix;
}

/** Human-readable target for error messages (client). */
export function describeApiTarget(): string {
  const base = getPublicApiBase();
  if (!base) {
    if (typeof window !== 'undefined') {
      return `${window.location.origin}/api (proxied to backend)`;
    }
    const backend = process.env.API_URL?.trim().replace(/\/$/, '');
    return backend ? `/api → ${backend}/api` : '/api → backend (Next.js proxy)';
  }
  return `${base}/api`;
}

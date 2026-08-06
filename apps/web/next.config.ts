import { loadEnvConfig } from '@next/env';
import type { NextConfig } from 'next';

// Load apps/web/.env.local before reading tunnel hostnames (written by dev-lan / dev-tunnel).
loadEnvConfig(process.cwd());

function hostnameFromUrl(url: string): string | undefined {
  return url.match(/^https?:\/\/([^/]+)/)?.[1];
}

const allowedDevOrigins = ['127.0.0.1', 'localhost'];

const lanOrigin = process.env.LAN_DEV_ORIGIN;
if (lanOrigin) {
  allowedDevOrigins.push(lanOrigin);
}

// Exact hostname required — *.trycloudflare.com wildcards are not reliably matched.
const tunnelHost =
  hostnameFromUrl(process.env.TUNNEL_WEB_URL ?? '') ??
  hostnameFromUrl(process.env.NEXTAUTH_URL ?? '');

if (tunnelHost) {
  allowedDevOrigins.push(tunnelHost);
}

const apiProxyTarget = (
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === 'production'
    ? 'https://doxed-founders-website-production.up.railway.app'
    : 'http://127.0.0.1:4000')
).replace(/\/$/, '');

function isLocalApiTarget(target: string) {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(target);
}

const apiRewrites = [
  {
    // Keep NextAuth OAuth routes + admin listing review on Next.js; proxy Nest /api/auth/login etc.
    source:
      '/api/:path((?!auth/(?:signin|signout|callback|session|csrf|providers|error)(?:/|$)|listing-applications/[^/]+/review$).*)',
    destination: `${apiProxyTarget}/api/:path*`,
  },
];

// 301 permanent redirects for renamed user-visible URL slugs.
// Internal API slugs (/api/founder-os/*, /api/founder-node/*) are intentionally
// NOT redirected — they are wire-protocol contracts. Only the user-facing
// /founder-os page route moved to /founder-ide. See docs/PRODUCTION-AI-KEYS.md
// §"URL changes" for the rationale.
const permanentRedirects = [
  { source: '/founder-os', destination: '/founder-ide', permanent: true },
  { source: '/founder-os/:path*', destination: '/founder-ide/:path*', permanent: true },
  // Legacy chat-dispatch surface — ported to /founder-ide. Keep deep links working.
  { source: '/founder-den', destination: '/founder-ide', permanent: true },
  { source: '/founder-den/:path*', destination: '/founder-ide', permanent: true },
  // Legacy Founder OS era downloads hub � replaced by /founder-ide (canonical download + pair surface).
  { source: '/downloads', destination: '/founder-ide', permanent: true },
  { source: '/downloads/:path*', destination: '/founder-ide', permanent: true },
  // Old developer docs redirect used to point at founder-den; bounce to IDE.
  { source: '/developers', destination: '/founder-ide', permanent: true },
  // 2FA / security now canonical on /account?tab=security. /settings/builder
  // page was retired (downloads/pairing moved into Founder IDE).
  {
    source: '/settings/builder',
    destination: '/account?tab=security',
    permanent: true,
  },
  {
    source: '/settings/builder/:path*',
    destination: '/account?tab=security',
    permanent: true,
  },
];

const securityHeaders = [
  { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains; preload' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(self), geolocation=(), payment=()',
  },
];

const nextConfig: NextConfig = {
  transpilePackages: ['@dcf/ui', '@dcf/types', '@dcf/config', '@dcf/utils', '@dcf/founder-vault'],
  serverExternalPackages: ['@capacitor/core', '@capacitor/filesystem', '@capacitor/preferences'],
  // Default static stale time is 300s — edge can serve old HTML while new JS hydrates (flash on hard refresh).
  experimental: {
    staleTimes: {
      dynamic: 0,
      static: 30,
    },
  },
  allowedDevOrigins,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
  async rewrites() {
    const useProxy =
      process.env.NODE_ENV === 'development'
        ? isLocalApiTarget(apiProxyTarget)
        : !isLocalApiTarget(apiProxyTarget);

    return useProxy ? apiRewrites : [];
  },
  async redirects() {
    return permanentRedirects;
  },
};

export default nextConfig;

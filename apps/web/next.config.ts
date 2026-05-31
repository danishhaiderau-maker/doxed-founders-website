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

const nextConfig: NextConfig = {
  transpilePackages: ['@dcf/ui', '@dcf/types', '@dcf/config', '@dcf/utils'],
  allowedDevOrigins,
  async rewrites() {
    const useProxy =
      process.env.NODE_ENV === 'development'
        ? isLocalApiTarget(apiProxyTarget)
        : !isLocalApiTarget(apiProxyTarget);

    return useProxy ? apiRewrites : [];
  },
};

export default nextConfig;

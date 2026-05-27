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

const apiProxyTarget = (process.env.API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '');

function isLocalApiTarget(target: string) {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(target);
}

const apiRewrites = [
  {
    // Keep /api/auth/* on Next.js (next-auth); proxy everything else to Nest.
    source: '/api/:path((?!auth(?:/|$)).*)',
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
        : Boolean(process.env.API_URL?.trim() && !isLocalApiTarget(apiProxyTarget));

    return useProxy ? apiRewrites : [];
  },
};

export default nextConfig;

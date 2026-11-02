import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { isApiDependentPath, isBackendApiRetired } from '@/lib/backend-api-retired';

function offlineFeatureForPath(pathname: string, search: string): string {
  if (pathname.startsWith('/login')) return 'login';
  if (pathname.startsWith('/register')) return 'register';
  if (pathname.startsWith('/account')) {
    if (search.includes('tab=connected')) return 'x-sync';
    return 'account';
  }
  if (pathname.startsWith('/docs/signal-api')) return 'x402';
  if (
    pathname.startsWith('/paper-trading') ||
    pathname.startsWith('/agent-hub') ||
    pathname.startsWith('/agents') ||
    pathname.startsWith('/portfolio') ||
    pathname.startsWith('/leaderboard') ||
    pathname.startsWith('/watchlist') ||
    pathname.startsWith('/predict')
  ) {
    return 'trading';
  }
  return 'generic';
}

export function middleware(request: NextRequest) {
  if (!isBackendApiRetired()) {
    return NextResponse.next();
  }

  const { pathname, search } = request.nextUrl;

  // NextAuth stays on Vercel (local session cookie only). Nest proxy is disabled separately.
  if (pathname.startsWith('/api/auth')) {
    return NextResponse.next();
  }

  // Any other `/api/*` is answered by the App Router 410 catch-all — do not rewrite to Railway.
  if (pathname.startsWith('/api/')) {
    return NextResponse.next();
  }

  if (pathname === '/platform-offline' || pathname.startsWith('/platform-offline/')) {
    return NextResponse.next();
  }

  if (isApiDependentPath(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = '/platform-offline';
    url.search = `?feature=${offlineFeatureForPath(pathname, search)}`;
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|manifest.json|downloads/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|json)$).*)',
  ],
};

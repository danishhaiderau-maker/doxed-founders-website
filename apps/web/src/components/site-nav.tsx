'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { useEffect, useRef, useState, Suspense } from 'react';
import { cn, resolveGamifiedRole } from '@dcf/utils';
import { fetchAccountOverview, AccountOverview } from '@/lib/api';
import { GamifiedRoleBadge } from '@/components/account/gamified-role-badge';
import { EngagementFlashLayer } from '@/components/engagement-flash-layer';

/** Row 1 — trading (amber family) */
const TRADING_NAV = [
  { href: '/discover', label: 'Discover' },
  { href: '/feed', label: 'Feed' },
  { href: '/paper-trading', label: 'Trading Alpha' },
  { href: '/leaderboard', label: 'Rankings' },
  { href: '/watchlist', label: 'Watchlist', auth: true },
  { hrefKey: 'portfolio' as const, label: 'Portfolio', auth: true },
] as const;

/** Row 2 — building (violet family) */
const BUILDING_NAV = [
  { href: '/founder-den', label: 'Founder OS', auth: true },
  { href: '/developers', label: 'Developers' },
  { href: '/founder-node', label: 'Founder Node' },
  { href: '/agents', label: 'Agents' },
  { href: '/raise-room', label: 'Raise Room' },
  { href: '/settings/builder', label: 'AI Stack', auth: true },
] as const;

/** Row 3 — scout / list + profile */
const ACTION_NAV = [
  {
    href: '/scout-votes',
    label: 'Scout vote',
    active: 'bg-sky-500 ring-2 ring-sky-300/50',
    idle: 'bg-sky-600 hover:bg-sky-500',
  },
  {
    href: '/list-your-project',
    label: 'List project',
    active: 'bg-violet-500 ring-2 ring-violet-300/50',
    idle: 'bg-violet-600 hover:bg-violet-500',
  },
] as const;

const PROFILE_LINKS = [
  { href: '/account', label: 'Overview' },
  { href: '/account?tab=security', label: 'Security' },
  { href: '/account?tab=notifications', label: 'Notification Settings' },
  { href: '/account?tab=connected', label: 'Connected Accounts' },
  { href: '/account?tab=points', label: 'DDollar earned' },
  { href: '/account?tab=reputation', label: 'Reputation' },
  { href: '/account?tab=activity', label: 'Activity History' },
] as const;

function navActive(pathname: string, href: string) {
  if (href === '/discover') return pathname === '/discover';
  if (href === '/feed') return pathname === '/feed' || pathname === '/build-feed';
  if (href === '/paper-trading') return pathname.startsWith('/paper-trading');
  if (href.startsWith('/portfolio/')) return pathname.startsWith('/portfolio/');
  if (href === '/leaderboard') return pathname.startsWith('/leaderboard');
  if (href === '/agents') return pathname.startsWith('/agents');
  if (href === '/founder-den') return pathname.startsWith('/founder-den');
  if (href === '/developers') return pathname.startsWith('/developers');
  if (href === '/raise-room') return pathname.startsWith('/raise-room');
  if (href === '/scout-votes') return pathname.startsWith('/scout-votes');
  if (href === '/settings/builder') return pathname.startsWith('/settings/builder');
  if (href.startsWith('/account')) return pathname.startsWith('/account');
  if (href === '/founder-node') return pathname.startsWith('/founder-node');
  if (href === '/watchlist') return pathname.startsWith('/watchlist');
  if (href === '/list-your-project') return pathname.startsWith('/list-your-project');
  return pathname === href || pathname.startsWith(`${href}/`);
}

function tradingLinkClass(active: boolean) {
  return active
    ? 'bg-amber-500/25 font-semibold text-amber-50 ring-1 ring-amber-400/50'
    : 'text-amber-200/75 hover:bg-amber-500/10 hover:text-amber-100';
}

function buildingLinkClass(active: boolean) {
  return active
    ? 'bg-violet-500/25 font-semibold text-violet-50 ring-1 ring-violet-400/50'
    : 'text-violet-200/75 hover:bg-violet-500/10 hover:text-violet-100';
}

export function SiteNav() {
  return (
    <>
      <Suspense fallback={<nav className="h-9 w-48 animate-pulse rounded-lg bg-zinc-800/50" />}>
        <SiteNavInner />
      </Suspense>
      <EngagementFlashLayer />
    </>
  );
}

function SiteNavInner() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const [profileOpen, setProfileOpen] = useState(false);
  const [accountPreview, setAccountPreview] = useState<AccountOverview | null>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  const portfolioUserId =
    session?.user?.id ?? accountPreview?.userId ?? getActiveUserId(session?.user?.id);

  useEffect(() => {
    if (!session?.accessToken) {
      setAccountPreview(null);
      return;
    }
    fetchAccountOverview(session.accessToken)
      .then(setAccountPreview)
      .catch(() => setAccountPreview(null));
  }, [session?.accessToken]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
    }
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);

  const profileActive = pathname.startsWith('/account') || pathname.startsWith('/settings');

  const fallbackRole = resolveGamifiedRole({
    platformRole: session?.user?.role,
  });

  return (
    <nav className="flex max-w-full flex-col items-end gap-2 text-sm md:gap-2.5">
      {/* Row 1 — Trading */}
      <div className="flex flex-wrap items-center justify-end gap-1.5 rounded-xl border border-amber-500/15 bg-amber-950/20 px-2 py-1.5 md:gap-2">
        {TRADING_NAV.map((item) => {
          if ('auth' in item && item.auth && !session) return null;
          if ('hrefKey' in item && item.hrefKey === 'portfolio') {
            if (!portfolioUserId) return null;
            const href = `/portfolio/${portfolioUserId}`;
            const active = navActive(pathname, href);
            return (
              <Link
                key="portfolio"
                href={href}
                className={cn('rounded-lg px-2.5 py-1.5 transition', tradingLinkClass(active))}
              >
                {item.label}
              </Link>
            );
          }
          if (!('href' in item)) return null;
          const href = item.href;
          const active = navActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              className={cn('rounded-lg px-2.5 py-1.5 transition', tradingLinkClass(active))}
            >
              {item.label}
            </Link>
          );
        })}
      </div>

      {/* Row 2 — Building */}
      <div className="flex flex-wrap items-center justify-end gap-1.5 rounded-xl border border-violet-500/15 bg-violet-950/20 px-2 py-1.5 md:gap-2">
        {BUILDING_NAV.map((item) => {
          if ('auth' in item && item.auth && !session) return null;
          const active = navActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn('rounded-lg px-2.5 py-1.5 transition', buildingLinkClass(active))}
            >
              {item.label}
            </Link>
          );
        })}
      </div>

      {/* Row 3 — Scout / list + profile */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {ACTION_NAV.map((item) => {
          const active = navActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'rounded-lg px-4 py-2 font-semibold text-white shadow-lg transition',
                active ? item.active : item.idle,
              )}
            >
              {item.label}
            </Link>
          );
        })}

        {session ? (
          <div className="relative border-l border-[var(--color-border)] pl-2 md:pl-3" ref={profileRef}>
            <button
              type="button"
              onClick={() => setProfileOpen((o) => !o)}
              className={cn(
                'flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition',
                profileActive || profileOpen
                  ? 'bg-zinc-700 font-semibold text-white ring-1 ring-zinc-500'
                  : 'text-zinc-300 hover:bg-zinc-800 hover:text-white',
              )}
            >
              <span className="hidden max-w-[140px] truncate sm:inline">
                {accountPreview?.username ?? session.user?.name ?? session.user?.email}
              </span>
              <GamifiedRoleBadge
                role={accountPreview?.gamifiedRole ?? fallbackRole}
                className="hidden sm:inline-flex"
              />
              <span className="sm:hidden">Profile</span>
            </button>
            {profileOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 min-w-[220px] rounded-xl border border-zinc-700 bg-zinc-950 py-1 shadow-xl">
                {accountPreview && (
                  <div className="border-b border-zinc-800 px-3 py-2">
                    <p className="truncate text-sm font-medium text-white">{accountPreview.username}</p>
                    <div className="mt-1">
                      <GamifiedRoleBadge role={accountPreview.gamifiedRole} />
                    </div>
                  </div>
                )}
                {PROFILE_LINKS.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setProfileOpen(false)}
                    className="block px-3 py-2 text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
                  >
                    {item.label}
                  </Link>
                ))}
                {isAdmin && (
                  <>
                    <Link
                      href="/admin/applications"
                      onClick={() => setProfileOpen(false)}
                      className="block px-3 py-2 text-sm text-amber-300/90 transition hover:bg-zinc-900"
                    >
                      Listing inbox
                    </Link>
                    <Link
                      href="/admin/platform"
                      onClick={() => setProfileOpen(false)}
                      className="block px-3 py-2 text-sm text-amber-300/90 transition hover:bg-zinc-900"
                    >
                      Treasury & top-ups
                    </Link>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => signOut({ callbackUrl: '/' })}
                  className="block w-full px-3 py-2 text-left text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        ) : (
          <Link
            href="/login"
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-[var(--color-muted)] hover:border-[var(--color-accent)] hover:text-white"
          >
            Sign in
          </Link>
        )}
      </div>
    </nav>
  );
}

export function SiteBrand({ className }: { className?: string }) {
  return (
    <Link href="/" className={cn('font-semibold tracking-tight text-white hover:text-[var(--color-accent)]', className)}>
      Doxxed crypto
    </Link>
  );
}

const SESSION_KEY = 'dcf-paper-user-id';

export function getActiveUserId(sessionUserId?: string | null): string | null {
  if (sessionUserId) return sessionUserId;
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(SESSION_KEY);
}

'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { useEffect, useRef, useState, Suspense } from 'react';
import { cn, resolveGamifiedRole } from '@dcf/utils';
import { fetchAccountOverview, AccountOverview } from '@/lib/api';
import { GamifiedRoleBadge } from '@/components/account/gamified-role-badge';
import { EngagementFlashLayer } from '@/components/engagement-flash-layer';
import { NotificationBell } from '@/components/notification-bell';

/** Row 1 — Explore */
const PRIMARY_NAV = [
  { href: '/discover', label: 'Discover' },
  { href: '/projects', label: 'Projects' },
  { href: '/leaderboard', label: 'Rankings' },
  { href: '/trust-center', label: 'Trust Center' },
  { href: '/agent-hub', label: 'Agents' },
] as const;

/** Row 2 — Trade & community */
const TRADING_NAV = [
  { href: '/feed', label: 'Feed' },
  { href: '/ddollar', label: 'DDollar' },
  { href: '/paper-trading', label: 'Trading Alpha' },
  { href: '/watchlist', label: 'Watchlist', auth: true },
  { hrefKey: 'portfolio' as const, label: 'Portfolio', auth: true },
] as const;

/** Row 3 — Build */
const BUILDING_NAV = [
  { href: '/founder-den', label: 'Founder OS', auth: true },
  { href: '/settings/builder', label: 'Founder Node', auth: true },
  { href: '/raise-room', label: 'Raise Room' },
  { href: '/list-your-project', label: 'List Project' },
] as const;

const PROFILE_LINKS = [
  { href: '/account', label: 'Overview' },
  { href: '/account?tab=security', label: 'Security' },
  { href: '/account?tab=notifications', label: 'Notification Settings' },
  { href: '/account?tab=connected', label: 'Connected Accounts' },
  { href: '/account?tab=reputation', label: 'Reputation' },
  { href: '/account?tab=activity', label: 'Activity History' },
] as const;

function navActive(pathname: string, href: string) {
  if (href === '/discover') return pathname === '/discover';
  if (href === '/feed') return pathname === '/feed' || pathname === '/build-feed' || pathname.startsWith('/town-hall');
  if (href === '/ddollar') return pathname.startsWith('/ddollar');
  if (href === '/paper-trading') return pathname.startsWith('/paper-trading');
  if (href.startsWith('/portfolio/')) return pathname.startsWith('/portfolio/');
  if (href === '/leaderboard') return pathname.startsWith('/leaderboard');
  if (href === '/agent-hub') return pathname.startsWith('/agent-hub') || pathname.startsWith('/agents');
  if (href === '/founder-den') {
    return (
      pathname.startsWith('/founder-den') ||
      pathname.startsWith('/settings/builder') ||
      pathname.startsWith('/founder-node') ||
      pathname.startsWith('/developers')
    );
  }
  if (href === '/raise-room') return pathname.startsWith('/raise-room');
  if (href === '/scout-votes') return pathname.startsWith('/scout-votes');
  if (href === '/trust-center') return pathname.startsWith('/trust-center') || pathname.startsWith('/scout-votes');
  if (href === '/projects') return pathname.startsWith('/projects');
  if (href.startsWith('/account')) return pathname.startsWith('/account');
  if (href === '/watchlist') return pathname.startsWith('/watchlist');
  if (href === '/list-your-project') return pathname.startsWith('/list-your-project');
  if (href === '/notifications') return pathname.startsWith('/notifications');
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

  const profileActive =
    pathname.startsWith('/account') ||
    (pathname.startsWith('/settings') && !pathname.startsWith('/settings/builder'));

  const fallbackRole = resolveGamifiedRole({
    platformRole: session?.user?.role,
  });

  return (
    <nav className="flex max-w-full flex-col items-end gap-2 text-sm md:gap-2.5">
      {/* Row 1 — Explore */}
      <div className="flex flex-wrap items-center justify-end gap-1.5 rounded-xl border border-zinc-700/40 bg-zinc-950/40 px-2 py-1.5 md:gap-2">
        {PRIMARY_NAV.map((item) => {
          if ('auth' in item && item.auth && !session) return null;
          const href = item.href;
          const active = navActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'rounded-lg px-2.5 py-1.5 transition',
                active
                  ? 'bg-zinc-100/10 font-semibold text-white ring-1 ring-zinc-400/40'
                  : 'text-zinc-300 hover:bg-zinc-800/60 hover:text-white',
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </div>

      {/* Row 2 — Trade & community */}
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

      {/* Row 3 — Build */}
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

      {/* Right — Notifications + Profile */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <NotificationBell />
        {session ? (
          <div className="relative" ref={profileRef}>
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
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <GamifiedRoleBadge role={accountPreview.gamifiedRole} />
                      <Link
                        href="/ddollar"
                        onClick={() => setProfileOpen(false)}
                        className="text-xs font-semibold text-amber-300 hover:underline"
                      >
                        {accountPreview.reputation.reputationPoints.toLocaleString()} DDollar
                      </Link>
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
                      href="/admin/control"
                      onClick={() => setProfileOpen(false)}
                      className="block border-t border-zinc-800 px-3 py-2 text-sm font-semibold text-amber-300/95 transition hover:bg-zinc-900"
                    >
                      Admin Control
                    </Link>
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

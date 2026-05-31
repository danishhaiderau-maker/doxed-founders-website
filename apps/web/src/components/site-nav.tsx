'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { useEffect, useRef, useState, Suspense } from 'react';
import { cn, resolveGamifiedRole } from '@dcf/utils';
import { fetchUnreadNotificationCount, fetchAccountOverview, AccountOverview } from '@/lib/api';
import { GamifiedRoleBadge } from '@/components/account/gamified-role-badge';
import { EngagementFlashLayer } from '@/components/engagement-flash-layer';

const PRIMARY_NAV = [
  { href: '/discover', label: 'Discover', tone: 'zinc' },
  { href: '/projects', label: 'Projects', tone: 'zinc' },
  { href: '/feed', label: 'Feed', tone: 'zinc' },
  { href: '/founder-den', label: 'Founder OS', auth: true, tone: 'violet' },
  { href: '/paper-trading', label: 'Trading Alpha', tone: 'amber' },
  { href: '/leaderboard', label: 'Rankings', tone: 'zinc' },
  { href: '/raise-room', label: 'Raise Room', tone: 'zinc' },
  { href: '/agents', label: 'Agents', tone: 'zinc' },
] as const;

const CTA_NAV = [
  {
    href: '/predict',
    label: 'Predict',
    active: 'bg-indigo-500 ring-2 ring-indigo-300/50',
    idle: 'bg-indigo-600 hover:bg-indigo-500',
  },
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
  {
    hrefKey: 'portfolio' as const,
    label: 'Portfolio',
    active: 'bg-emerald-500 ring-2 ring-emerald-300/50',
    idle: 'bg-emerald-600 hover:bg-emerald-500',
  },
  {
    href: '/founder-node',
    label: 'Founder Node',
    active: 'bg-cyan-500 ring-2 ring-cyan-300/50',
    idle: 'bg-cyan-600 hover:bg-cyan-500',
  },
] as const;

const MORE_NAV = [{ href: '/watchlist', label: 'Watchlist', auth: true }] as const;

const PROFILE_LINKS = [
  { href: '/account', label: 'Overview' },
  { href: '/account?tab=security', label: 'Security' },
  { href: '/account?tab=notifications', label: 'Notification Settings' },
  { href: '/account?tab=connected', label: 'Connected Accounts' },
  { href: '/account?tab=points', label: 'Points & Rewards' },
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
  if (href === '/raise-room') return pathname.startsWith('/raise-room');
  if (href === '/scout-votes') return pathname.startsWith('/scout-votes');
  if (href === '/predict') return pathname.startsWith('/predict');
  if (href === '/projects') return pathname.startsWith('/project') || pathname === '/projects';
  if (href.startsWith('/account')) return pathname.startsWith('/account');
  if (href === '/founder-node') return pathname.startsWith('/founder-node');
  return pathname === href || pathname.startsWith(`${href}/`);
}

function primaryLinkClass(active: boolean, tone: string) {
  if (active) {
    return 'bg-white/10 font-semibold text-white ring-1 ring-white/25';
  }
  if (tone === 'violet') return 'text-violet-300/90 hover:bg-violet-500/15 hover:text-violet-100';
  if (tone === 'amber') return 'text-amber-300/90 hover:bg-amber-500/15 hover:text-amber-100';
  return 'text-[var(--color-muted)] hover:bg-white/5 hover:text-white';
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
  const [unread, setUnread] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [accountPreview, setAccountPreview] = useState<AccountOverview | null>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  const portfolioUserId =
    session?.user?.id ?? accountPreview?.userId ?? getActiveUserId(session?.user?.id);

  useEffect(() => {
    if (!session?.accessToken) {
      setUnread(0);
      setAccountPreview(null);
      return;
    }
    const refresh = () => {
      fetchUnreadNotificationCount(session.accessToken!)
        .then((r) => setUnread(r.count))
        .catch(() => setUnread(0));
    };
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [session?.accessToken]);

  useEffect(() => {
    if (!session?.accessToken) return;
    fetchAccountOverview(session.accessToken)
      .then(setAccountPreview)
      .catch(() => setAccountPreview(null));
  }, [session?.accessToken]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
    }
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);

  const moreVisible = MORE_NAV.filter((item) => !('auth' in item && item.auth && !session));
  const moreActive =
    moreVisible.some((item) => navActive(pathname, item.href)) ||
    (isAdmin && pathname.startsWith('/admin'));

  const profileActive = pathname.startsWith('/account') || pathname.startsWith('/settings');

  const fallbackRole = resolveGamifiedRole({
    platformRole: session?.user?.role,
  });

  return (
    <nav className="flex max-w-full flex-col items-end gap-2 text-sm md:gap-2.5">
      <div className="flex flex-wrap items-center justify-end gap-2 md:gap-2.5">
        {PRIMARY_NAV.map((item) => {
          if ('auth' in item && item.auth && !session) return null;
          const active = navActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'rounded-lg px-2.5 py-1.5 transition',
                primaryLinkClass(active, item.tone),
              )}
            >
              {item.label}
            </Link>
          );
        })}

        {session && (
          <Link
            href="/notifications"
            className={cn(
              'relative rounded-lg px-2.5 py-1.5 transition',
              pathname === '/notifications'
                ? 'bg-emerald-500/25 font-semibold text-emerald-50 ring-1 ring-emerald-400/50'
                : 'text-emerald-200/80 hover:bg-emerald-500/15 hover:text-emerald-100',
            )}
          >
            Notifications
            {unread > 0 && (
              <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-black">
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </Link>
        )}

        <div className="relative" ref={moreRef}>
          <button
            type="button"
            onClick={() => setMoreOpen((o) => !o)}
            className={cn(
              'rounded-lg px-2.5 py-1.5 transition',
              moreActive || moreOpen
                ? 'bg-zinc-700 font-semibold text-white ring-1 ring-zinc-500'
                : 'text-zinc-300 hover:bg-zinc-800 hover:text-white',
            )}
          >
            More
          </button>
          {moreOpen && (
            <div className="absolute right-0 top-full z-50 mt-1 min-w-[168px] rounded-xl border border-zinc-700 bg-zinc-950 py-1 shadow-xl">
              {moreVisible.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMoreOpen(false)}
                  className={cn(
                    'flex items-center justify-between px-3 py-2 text-sm transition hover:bg-zinc-900',
                    navActive(pathname, item.href) ? 'text-white' : 'text-zinc-400',
                  )}
                >
                  {item.label}
                </Link>
              ))}
              {isAdmin && (
                <>
                  <Link
                    href="/admin/applications"
                    onClick={() => setMoreOpen(false)}
                    className={cn(
                      'flex items-center justify-between px-3 py-2 text-sm transition hover:bg-zinc-900',
                      pathname.startsWith('/admin/applications') ? 'text-amber-200' : 'text-zinc-400',
                    )}
                  >
                    Listing inbox
                  </Link>
                  <Link
                    href="/admin/platform"
                    onClick={() => setMoreOpen(false)}
                    className={cn(
                      'flex items-center justify-between px-3 py-2 text-sm transition hover:bg-zinc-900',
                      pathname.startsWith('/admin/platform') ? 'text-amber-200' : 'text-zinc-400',
                    )}
                  >
                    Treasury & top-ups
                  </Link>
                </>
              )}
            </div>
          )}
        </div>

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
              <span className="hidden max-w-[120px] truncate sm:inline">
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
                {portfolioUserId && (
                  <Link
                    href={`/portfolio/${portfolioUserId}`}
                    onClick={() => setProfileOpen(false)}
                    className="block px-3 py-2 text-sm text-emerald-300 transition hover:bg-zinc-900 hover:text-emerald-200"
                  >
                    Trading Portfolio
                  </Link>
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

      <div className="flex flex-wrap items-center justify-end gap-2">
        {CTA_NAV.map((item) => {
          if ('hrefKey' in item && item.hrefKey === 'portfolio') {
            if (!portfolioUserId) return null;
            const href = `/portfolio/${portfolioUserId}`;
            const active = navActive(pathname, href);
            return (
              <Link
                key="portfolio"
                href={href}
                className={cn(
                  'rounded-lg px-4 py-2 font-semibold text-white shadow-lg transition',
                  active ? item.active : item.idle,
                )}
              >
                {item.label}
              </Link>
            );
          }
          const href = 'href' in item ? item.href : '/';
          const active = navActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'rounded-lg px-4 py-2 font-semibold text-white shadow-lg transition',
                active ? item.active : item.idle,
              )}
            >
              {item.label}
            </Link>
          );
        })}
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

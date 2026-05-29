'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { cn } from '@dcf/utils';
import { fetchUnreadNotificationCount } from '@/lib/api';
import { AccountLabel } from '@/components/account-welcome';

const SESSION_KEY = 'dcf-paper-user-id';

const NAV_LINKS = [
  { href: '/projects', label: 'Projects' },
  { href: '/build-feed', label: 'Build feed' },
  { href: '/founder-den', label: 'Founder Den', auth: true },
  { href: '/feed', label: 'Feed' },
  { href: '/paper-trading', label: 'Trade' },
  { href: '/watchlist', label: 'Watchlist', auth: true },
  { href: '/notifications', label: 'Alerts', auth: true },
  { href: '/scout-votes', label: 'Scout votes' },
  { href: '/reputation', label: 'Points' },
  { href: '/leaderboard', label: 'Leaderboard' },
] as const;

function navActive(pathname: string, href: string) {
  if (href === '/feed') return pathname === '/feed';
  if (href === '/paper-trading') return pathname.startsWith('/paper-trading');
  if (href === '/projects') return pathname.startsWith('/project') || pathname === '/projects';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteNav() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    if (!session?.accessToken) {
      setUnread(0);
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

  return (
    <nav className="flex flex-wrap items-center gap-2 text-sm md:gap-2.5">
      {NAV_LINKS.map((item) => {
        if ('auth' in item && item.auth && !session) return null;
        const active = navActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'rounded-lg px-2.5 py-1.5 transition',
              active
                ? 'bg-[var(--color-accent)]/20 font-semibold text-white ring-1 ring-[var(--color-accent)]/50'
                : 'text-[var(--color-muted)] hover:bg-white/5 hover:text-white',
            )}
          >
            {item.label}
            {item.href === '/notifications' && unread > 0 && (
              <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-black">
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </Link>
        );
      })}
      {isAdmin && (
        <Link
          href="/admin/applications"
          className={cn(
            'rounded-lg px-2.5 py-1.5 transition',
            pathname.startsWith('/admin')
              ? 'bg-amber-500/20 font-semibold text-amber-100 ring-1 ring-amber-500/40'
              : 'text-[var(--color-muted)] hover:text-white',
          )}
        >
          Admin
        </Link>
      )}
      {session ? (
        <div className="flex items-center gap-2 border-l border-[var(--color-border)] pl-2 md:gap-3 md:pl-3">
          <span className="hidden text-[var(--color-muted)] sm:inline">
            <AccountLabel name={session.user?.name} email={session.user?.email} />
          </span>
          <Link
            href={`/portfolio/${session.user?.id}`}
            className={cn(
              'hidden rounded-lg px-2.5 py-1 text-xs sm:inline',
              pathname.startsWith('/portfolio')
                ? 'bg-amber-500/25 font-semibold text-amber-100 ring-1 ring-amber-500/40'
                : 'border border-amber-500/30 text-amber-200',
            )}
          >
            Profile
          </Link>
          <button
            type="button"
            onClick={() => signOut({ callbackUrl: '/' })}
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-[var(--color-muted)] hover:text-white"
          >
            Sign out
          </button>
        </div>
      ) : (
        <Link
          href="/login"
          className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-[var(--color-muted)] hover:border-[var(--color-accent)] hover:text-white"
        >
          Sign in
        </Link>
      )}
      <Link
        href="/list-your-project"
        className="rounded-lg bg-[var(--color-accent)] px-4 py-2 font-medium text-white hover:bg-[var(--color-accent-hover)]"
      >
        List project
      </Link>
    </nav>
  );
}

export function getActiveUserId(sessionUserId?: string | null): string | null {
  if (sessionUserId) return sessionUserId;
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(SESSION_KEY);
}

export function SiteBrand({ className }: { className?: string }) {
  return (
    <Link href="/" className={cn('font-semibold tracking-tight text-white hover:text-[var(--color-accent)]', className)}>
      Doxxed crypto
    </Link>
  );
}

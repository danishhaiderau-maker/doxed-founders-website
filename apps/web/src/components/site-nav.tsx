'use client';

import Link from 'next/link';
import { useSession, signOut } from 'next-auth/react';
import { AccountLabel } from '@/components/account-welcome';

const SESSION_KEY = 'dcf-paper-user-id';

export function SiteNav() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';

  return (
    <nav className="flex flex-wrap items-center gap-3 text-sm md:gap-4">
      <Link href="/projects" className="text-[var(--color-muted)] hover:text-white">
        Projects
      </Link>
      <Link href="/feed" className="font-medium text-[var(--color-accent)] hover:text-white">
        Feed
      </Link>
      <Link href="/paper-trading" className="text-[var(--color-muted)] hover:text-white">
        Trade
      </Link>
      {session && (
        <Link href="/watchlist" className="text-[var(--color-muted)] hover:text-white">
          Watchlist
        </Link>
      )}
      <Link href="/leaderboard" className="text-[var(--color-muted)] hover:text-white">
        Leaderboard
      </Link>
      {isAdmin && (
        <Link href="/admin/applications" className="text-[var(--color-muted)] hover:text-white">
          Admin
        </Link>
      )}
      {session ? (
        <div className="flex items-center gap-3">
          <span className="hidden text-[var(--color-muted)] sm:inline">
            <AccountLabel name={session.user?.name} email={session.user?.email} />
          </span>
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

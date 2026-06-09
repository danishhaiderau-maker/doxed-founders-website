'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { SiteBrand } from '@/components/site-nav';

const NAV = [
  { href: '/founder-den', label: 'Founder OS' },
  { href: '/account', label: 'Dashboard' },
  { href: '/agent-hub', label: 'Agent Hub', match: '/agent-hub' },
  { href: '/scout-votes', label: 'Scout Voting' },
  { href: '/predict', label: 'Prediction Markets' },
  { href: '/discover', label: 'Discover' },
  { href: '/feed', label: 'Feed' },
  { href: '/account', label: 'Portfolio', auth: true },
  { href: '/ddollar', label: 'DDollar' },
  { href: '/trust-center', label: 'Trust Center' },
  { href: '/settings/builder', label: 'Founder Node', auth: true },
  { href: '/account?tab=security', label: 'Settings', auth: true },
] as const;

export function AgentHubShell({
  children,
  searchPlaceholder = 'Search agents, strategies, or builders…',
}: {
  children: React.ReactNode;
  searchPlaceholder?: string;
}) {
  const pathname = usePathname();
  const { data: session } = useSession();
  const signedIn = Boolean(session?.accessToken);
  const isAdmin = session?.user?.role === 'ADMIN';
  const displayName = session?.user?.name?.split(' ')[0] ?? 'Account';

  return (
    <div className="flex min-h-screen bg-[#050508] text-white">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-zinc-800/80 bg-[#07070c] lg:flex">
        <div className="border-b border-zinc-800/80 px-4 py-5">
          <SiteBrand className="text-sm" />
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
          {NAV.map((item) => {
            if ('auth' in item && item.auth && !signedIn) return null;
            const active =
              'match' in item
                ? pathname.startsWith(item.match)
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={`${item.href}-${item.label}`}
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm transition ${
                  active
                    ? 'bg-violet-600/25 font-semibold text-violet-100 ring-1 ring-violet-500/30'
                    : 'text-zinc-400 hover:bg-zinc-900 hover:text-white'
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-zinc-800/80 p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">DDollar balance</p>
          <Link href="/ddollar" className="mt-1 block">
            <span className="text-lg font-bold text-emerald-400">₿ 12,450</span>
            <span className="mt-0.5 block text-xs text-zinc-500">$1,245.00 · +3.45% 24h</span>
          </Link>
          <Link
            href="/list-your-project"
            className="mt-4 block rounded-xl border border-violet-500/30 bg-violet-950/30 p-3 text-xs text-violet-200 hover:border-violet-400/50"
          >
            <span className="font-semibold">Builder?</span>
            <span className="mt-1 block text-zinc-500">Submit your agent →</span>
          </Link>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
          <div className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
            <div className="hidden shrink-0 sm:block">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-violet-300">Agent Hub</p>
              <p className="text-[10px] text-zinc-500">Discover · Evaluate · Hire · Profit</p>
            </div>
            <div className="lg:hidden">
              <SiteBrand className="text-xs" />
            </div>
            <input
              type="search"
              placeholder={searchPlaceholder}
              className="min-w-[180px] flex-1 rounded-xl border border-zinc-800 bg-zinc-950/80 px-4 py-2 text-sm text-zinc-300 placeholder:text-zinc-600"
            />
            <div className="flex items-center gap-2">
              <Link
                href="/watchlist"
                className="hidden rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:text-white sm:inline-block"
              >
                Watchlist
              </Link>
              <Link
                href="/notifications"
                className="relative rounded-lg border border-zinc-700 px-2.5 py-1.5 text-sm text-zinc-300 hover:text-white"
                aria-label="Notifications"
              >
                🔔
                <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-red-500" />
              </Link>
              <Link
                href={signedIn ? '/account' : '/login'}
                className="flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:text-white"
              >
                <span className="hidden sm:inline">{displayName}</span>
                {isAdmin && (
                  <span className="rounded bg-violet-600/40 px-1.5 py-0.5 text-[10px] font-bold uppercase text-violet-200">
                    Admin
                  </span>
                )}
              </Link>
            </div>
          </div>
        </header>
        <div className="flex-1">{children}</div>
      </div>
    </div>
  );
}

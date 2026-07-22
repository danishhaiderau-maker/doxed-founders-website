'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search } from 'lucide-react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AgentDirectoryBadges } from '@/components/agent-directory-badges';
import { DdollarBalanceSidebar } from '@/components/ddollar/ddollar-balance-sidebar';

const DISCOVER_NAV = [
  { href: '/agent-hub', label: 'Agents' },
  { href: '/projects', label: 'Projects' },
  { href: '/founders', label: 'Founders' },
  { href: '/trust-center', label: 'Trust Center' },
  { href: '/feed', label: 'Updates' },
] as const;

export function AgentHubShell({
  children,
  searchPlaceholder = 'Search agents, strategies, or builders...',
}: {
  children: React.ReactNode;
  searchPlaceholder?: string;
}) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen bg-[#0b0c0e] text-white">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-zinc-800/80 bg-[#111214] lg:flex">
        <div className="border-b border-zinc-800/80 px-4 py-5">
          <SiteBrand className="text-sm" />
          <p className="mt-4 text-xs font-semibold text-zinc-200">Discover</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">Projects, people, agents, and evidence.</p>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Discover">
          {DISCOVER_NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm transition ${
                  active
                    ? 'bg-amber-500/15 font-semibold text-amber-100 ring-1 ring-amber-400/30'
                    : 'text-zinc-400 hover:bg-zinc-800/70 hover:text-white'
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-zinc-800/80 p-4">
          <p className="text-xs font-semibold text-zinc-400">DDollar</p>
          <DdollarBalanceSidebar />
          <Link
            href="/list-your-project"
            className="mt-4 block rounded-lg border border-blue-500/25 bg-blue-950/20 p-3 text-xs text-blue-100 transition hover:border-blue-400/50"
          >
            <span className="font-semibold">List your project</span>
            <span className="mt-1 block text-zinc-500">Start launch readiness</span>
          </Link>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-[#0b0c0e]/95 backdrop-blur-xl">
          <div className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
            <div className="lg:hidden">
              <SiteBrand className="text-xs" />
            </div>
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
              <input
                type="search"
                placeholder={searchPlaceholder}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950/70 py-2 pl-9 pr-3 text-sm text-zinc-200 outline-none transition placeholder:text-zinc-600 focus:border-blue-500/60"
              />
            </div>
            <SiteNav />
          </div>
        </header>
        <div className="flex-1">{children}</div>
        <footer className="border-t border-zinc-800/80 px-4 py-4 sm:px-6">
          <AgentDirectoryBadges />
        </footer>
      </div>
    </div>
  );
}

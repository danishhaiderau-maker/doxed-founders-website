'use client';

import { AgentDirectoryBadges } from '@/components/agent-directory-badges';
import { SiteBrand, SiteNav } from '@/components/site-nav';

export function AgentHubShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-50 border-b border-white/10 bg-[#050508]/85 backdrop-blur-2xl">
        <div className="mx-auto flex min-h-16 w-full min-w-0 max-w-[1600px] items-center justify-between gap-3 overflow-hidden px-4 sm:gap-4 sm:px-6 lg:px-8">
          <SiteBrand className="shrink-0 text-sm font-bold uppercase tracking-tight" />
          <SiteNav />
        </div>
      </header>

      <main>{children}</main>

      <footer className="border-t border-white/10">
        <div className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
          <AgentDirectoryBadges />
        </div>
      </footer>
    </div>
  );
}

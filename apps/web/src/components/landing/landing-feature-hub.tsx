'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { HUB_NAV_ROWS } from '@/components/hub-nav-config';
import { getActiveUserId } from '@/components/site-nav';
import { useFeedNewCount } from '@/hooks/use-feed-new-count';
import { LandingHubPreviewWidgets } from '@/components/landing/landing-hub-preview-widgets';
import type { PlatformStats } from '@/lib/api';

type HubProps = {
  scoutPending?: number;
  platformStats?: PlatformStats | null;
};

function NavTile({
  href,
  label,
  icon,
  badge,
  accent,
}: {
  href: string;
  label: string;
  icon: string;
  badge?: string;
  accent: 'gray' | 'gold' | 'purple';
}) {
  const accentClass =
    accent === 'gold'
      ? 'border-amber-500/15 bg-amber-950/15 hover:border-amber-500/40 hover:bg-amber-950/30'
      : accent === 'purple'
        ? 'border-violet-500/15 bg-violet-950/15 hover:border-violet-500/40 hover:bg-violet-950/30'
        : 'border-zinc-800/80 bg-zinc-950/40 hover:border-zinc-600 hover:bg-zinc-900/60';

  return (
    <Link
      href={href}
      className={`group flex items-center gap-2.5 rounded-xl border px-3 py-2.5 transition ${accentClass}`}
    >
      <span className="text-lg leading-none opacity-90" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1 text-xs font-semibold text-zinc-100 group-hover:text-white">{label}</span>
      {badge ? (
        <span className="rounded-full bg-violet-500/90 px-1.5 py-0.5 text-[8px] font-bold text-white">{badge}</span>
      ) : null}
    </Link>
  );
}

/** Grouped quick-link hub — three columns instead of stacked rows. */
export function LandingHubNavTable({ scoutPending = 0 }: { scoutPending?: number }) {
  const { data: session } = useSession();
  const { count: feedNewCount } = useFeedNewCount();
  const portfolioUserId = session?.user?.id ?? getActiveUserId(session?.user?.id);

  function resolveHref(item: (typeof HUB_NAV_ROWS)[number]['items'][number]) {
    if (item.href === '__portfolio__') {
      return portfolioUserId ? `/portfolio/${portfolioUserId}` : '/login?callbackUrl=%2Fpaper-trading';
    }
    if (item.auth && !session) {
      return `/login?callbackUrl=${encodeURIComponent(item.href)}`;
    }
    return item.href;
  }

  function rowAccent(id: string): 'gray' | 'gold' | 'purple' {
    if (id === 'trading') return 'gold';
    if (id === 'build') return 'purple';
    return 'gray';
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="border-b border-zinc-800/70 px-4 py-3 sm:px-5">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">Platform hub</p>
        <p className="mt-0.5 text-sm text-zinc-400">Everything on Doxxed Crypto — grouped for quick access</p>
      </div>
      <div className="grid gap-3 p-3 sm:p-4 lg:grid-cols-3">
        {HUB_NAV_ROWS.map((row) => (
          <div
            key={row.id}
            className={`flex flex-col rounded-xl border p-3 ${row.borderClass} ${row.rowBgClass}`}
          >
            <div className="mb-3">
              <p className={`text-xs font-bold uppercase tracking-wide ${row.labelClass}`}>{row.label}</p>
              <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">{row.sidebarDescription}</p>
            </div>
            <div className="grid flex-1 gap-1.5 sm:grid-cols-1">
              {row.items.map((item) => {
                const badge =
                  item.label === 'Feed' && feedNewCount > 0
                    ? feedNewCount > 9
                      ? '9+'
                      : String(feedNewCount)
                    : item.label === 'Trust Center' && scoutPending > 0
                      ? String(scoutPending)
                      : undefined;
                return (
                  <NavTile
                    key={`${item.label}-${item.href}`}
                    href={resolveHref(item)}
                    label={item.label}
                    icon={item.icon}
                    badge={badge}
                    accent={rowAccent(row.id)}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Feed · DDollar · Trust · Founder OS preview widgets. */
export function LandingHubPreviews({ scoutPending = 0, platformStats = null }: HubProps) {
  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="bg-black/30 p-2 sm:p-3">
        <LandingHubPreviewWidgets scoutPending={scoutPending} platformStats={platformStats} />
      </div>
    </section>
  );
}

/** Nav table + previews together (legacy combined block). */
export function LandingFeatureHub({ scoutPending = 0, platformStats = null }: HubProps) {
  return (
    <>
      <LandingHubNavTable scoutPending={scoutPending} />
      <LandingHubPreviews scoutPending={scoutPending} platformStats={platformStats} />
    </>
  );
}

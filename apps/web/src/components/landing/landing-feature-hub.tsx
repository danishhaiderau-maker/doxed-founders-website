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

function NavPill({
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
      ? 'hover:border-amber-500/50 hover:bg-amber-950/40'
      : accent === 'purple'
        ? 'hover:border-violet-500/50 hover:bg-violet-950/40'
        : 'hover:border-zinc-500 hover:bg-zinc-900';

  return (
    <Link
      href={href}
      className={`group inline-flex items-center gap-1.5 rounded-lg border border-zinc-700/70 bg-zinc-950/60 px-2.5 py-1.5 transition ${accentClass}`}
    >
      <span className="text-sm leading-none opacity-90" aria-hidden>
        {icon}
      </span>
      <span className="text-[11px] font-semibold text-zinc-100 group-hover:text-white">{label}</span>
      {badge ? (
        <span className="rounded-full bg-amber-500/25 px-1.5 py-0.5 text-[8px] font-bold text-amber-100">{badge}</span>
      ) : null}
    </Link>
  );
}

/** Three-row navigation table (Explore · Community · Build). */
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
      <div className="grid lg:grid-cols-[1fr_minmax(11rem,13rem)]">
        <div className="divide-y divide-zinc-800/70">
          {HUB_NAV_ROWS.map((row) => (
            <div key={row.id} className={`flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:gap-4 sm:px-4 ${row.rowBgClass}`}>
              <div className="shrink-0 sm:w-[7.5rem]">
                <p className="text-[9px] font-bold uppercase tracking-[0.22em] text-zinc-600">{row.rowNumber}</p>
                <p className={`text-xs font-bold uppercase tracking-wide ${row.labelClass}`}>{row.label}</p>
              </div>
              <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
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
                    <NavPill
                      key={item.label}
                      href={resolveHref(item)}
                      label={item.label}
                      icon={item.icon}
                      badge={badge}
                      accent={rowAccent(row.id)}
                    />
                  );
                })}
              </div>
              <p className="text-[10px] text-zinc-500 lg:hidden">{row.subtitle}</p>
            </div>
          ))}
        </div>

        <aside className="hidden border-l border-zinc-800/70 bg-black/20 px-3 py-4 lg:block">
          <p className="text-[9px] font-bold uppercase tracking-[0.2em] text-zinc-600">Navigation</p>
          <ul className="mt-3 space-y-4">
            {HUB_NAV_ROWS.map((row) => (
              <li key={row.id}>
                <p className={`text-[10px] font-bold uppercase tracking-wide ${row.labelClass}`}>{row.label}</p>
                <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">{row.sidebarDescription}</p>
              </li>
            ))}
          </ul>
        </aside>
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

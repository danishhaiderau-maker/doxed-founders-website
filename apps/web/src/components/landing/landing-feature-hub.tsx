'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { HUB_NAV_ROWS } from '@/components/hub-nav-config';
import { getActiveUserId } from '@/components/site-nav';

type Props = {
  scoutPending?: number;
};

function HubNavButton({
  href,
  label,
  icon,
  badge,
}: {
  href: string;
  label: string;
  icon: string;
  badge?: string;
}) {
  return (
    <Link
      href={href}
      className="group flex min-h-[4.5rem] flex-col items-center justify-center rounded-xl border border-zinc-700/80 bg-zinc-950/80 px-2 py-3 text-center transition hover:border-zinc-500 hover:bg-zinc-900"
    >
      <span className="text-xl leading-none" aria-hidden>
        {icon}
      </span>
      <span className="mt-2 text-[11px] font-semibold leading-tight text-zinc-100 group-hover:text-white">
        {label}
      </span>
      {badge ? (
        <span className="mt-1 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-medium text-amber-200">
          {badge}
        </span>
      ) : null}
    </Link>
  );
}

export function LandingFeatureHub({ scoutPending = 0 }: Props) {
  const { data: session } = useSession();
  const portfolioUserId = session?.user?.id ?? getActiveUserId(session?.user?.id);

  return (
    <section className="rounded-2xl border border-zinc-800/90 bg-zinc-950/70 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] sm:p-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.28em] text-zinc-500">Mission control</p>
          <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">Choose your destination</h2>
        </div>
        {scoutPending > 0 ? (
          <Link
            href="/trust-center?tab=scout-voting"
            className="rounded-lg border border-amber-500/40 bg-amber-950/30 px-3 py-1.5 text-xs font-semibold text-amber-100 hover:border-amber-400/60"
          >
            Scout voting ({scoutPending})
          </Link>
        ) : null}
      </div>

      <div className="mt-4 space-y-3">
        {HUB_NAV_ROWS.map((row) => (
          <div key={row.id} className={`rounded-xl border p-3 ${row.borderClass}`}>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <p className={`text-[10px] font-bold uppercase tracking-[0.2em] ${row.labelClass}`}>{row.label}</p>
              <p className="text-[10px] text-zinc-500">{row.subtitle}</p>
            </div>
            <div
              className={`grid gap-2 ${
                row.items.length >= 5 ? 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5' : 'grid-cols-2 sm:grid-cols-3'
              }`}
            >
              {row.items.map((item) => {
                let href = item.href;
                if (item.href === '__portfolio__') {
                  href = portfolioUserId
                    ? `/portfolio/${portfolioUserId}`
                    : '/login?callbackUrl=%2Fpaper-trading';
                } else if (item.auth && !session) {
                  href = `/login?callbackUrl=${encodeURIComponent(item.href)}`;
                }
                const badge =
                  item.label === 'Trust Center' && scoutPending > 0
                    ? `${scoutPending} pending`
                    : undefined;
                return (
                  <HubNavButton
                    key={item.label}
                    href={href}
                    label={item.label}
                    icon={item.icon}
                    badge={badge}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <p className="mt-3 text-center text-[10px] text-zinc-600">
        Tap any tile — destination pages show a Home button to return here.
      </p>
    </section>
  );
}

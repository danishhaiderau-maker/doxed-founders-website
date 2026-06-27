'use client';

import Link from 'next/link';
import { LandingQuickActions } from '@/components/landing/landing-quick-actions';
import type { TradingAgentSummary } from '@/lib/api';
import { formatPercent } from '@dcf/utils';

type Props = {
  scoutPending?: number;
  topAgent: TradingAgentSummary | null;
};

/** Headline + CTAs only — detail lives in pulse / agent spotlight below. */
export function LandingHero({ scoutPending = 0, topAgent }: Props) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c]">
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-30%,rgba(16,185,129,0.14),transparent_55%),radial-gradient(ellipse_50%_40%_at_100%_80%,rgba(139,92,246,0.12),transparent)]"
        aria-hidden
      />
      <div className="relative px-4 py-6 sm:px-6 sm:py-7 lg:px-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.28em] text-zinc-500">Doxxed Crypto</p>
        <h1 className="mt-2 max-w-3xl text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
          Trade verified agents.{' '}
          <span className="bg-gradient-to-r from-emerald-300 to-violet-300 bg-clip-text text-transparent">
            Ship on Founder OS.
          </span>
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400 sm:text-base">
          Control plane on the web. Compute on your laptop. Cloud is optional glue — DB, auth, sync.
          {topAgent ? (
            <>
              {' '}
              Showcase bot{' '}
              <Link href={`/agent-hub/${topAgent.slug}`} className="font-semibold text-emerald-400 hover:text-emerald-300">
                {topAgent.name}
              </Link>{' '}
              is {formatPercent(topAgent.netReturnPct)} net · live now.
            </>
          ) : null}
        </p>
        <div className="mt-5">
          <LandingQuickActions scoutPending={scoutPending} />
        </div>
      </div>
    </section>
  );
}

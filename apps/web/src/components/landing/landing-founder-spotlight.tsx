'use client';

import Link from 'next/link';
import { ONBOARDING_PATHS } from '@dcf/utils';

const PATH_LINKS: Record<string, string> = {
  SOVEREIGN: '/founder-den?onboard=sovereign',
  BYO_CLOUD: '/founder-den?onboard=byo',
  MIGRATE_PRIVATE: '/founder-den?onboard=migrate',
  FREE_STARTER: '/founder-den?onboard=starter',
  FOUNDER_CLOUD: '/founder-den?onboard=founder',
};

function computeBadge(mode: string) {
  if (mode === 'LOCAL') return { label: '$0 AI', className: 'text-emerald-400 border-emerald-500/30' };
  if (mode === 'HYBRID') return { label: 'Hybrid', className: 'text-sky-400 border-sky-500/30' };
  return { label: 'Cloud', className: 'text-amber-400 border-amber-500/30' };
}

/** Single Founder OS block — paths only; no duplicate CTAs (hero + header cover actions). */
export function LandingFounderSpotlight() {
  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/20 bg-gradient-to-b from-violet-950/20 to-zinc-950/80">
      <div className="border-b border-violet-500/10 px-4 py-3 sm:px-5">
        <h2 className="text-sm font-bold text-white sm:text-base">Founder OS — pick your infrastructure path</h2>
        <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-zinc-500">
          Sovereign · local vault + Ollama. BYO Cloud · connect Vercel/Railway. Free Starter · live URL in minutes.
        </p>
      </div>
      <div className="grid gap-2 p-3 sm:grid-cols-2 sm:p-4 lg:grid-cols-5">
        {ONBOARDING_PATHS.map((path) => {
          const badge = computeBadge(path.computePlane);
          return (
            <Link
              key={path.id}
              href={PATH_LINKS[path.id] ?? '/founder-den'}
              className="group rounded-xl border border-zinc-800/80 bg-black/30 p-3 transition hover:border-violet-500/35 hover:bg-violet-950/15"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="text-lg" aria-hidden>
                  {path.icon}
                </span>
                <span className={`rounded border px-1 py-0.5 text-[7px] font-bold uppercase ${badge.className}`}>
                  {badge.label}
                </span>
              </div>
              <p className="mt-2 text-xs font-bold text-white group-hover:text-violet-50">{path.title}</p>
              <p className="mt-1 line-clamp-2 text-[10px] text-zinc-500">{path.tagline}</p>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

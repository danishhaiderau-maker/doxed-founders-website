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
  if (mode === 'LOCAL') return { label: 'Local · $0 AI', className: 'text-emerald-400 bg-emerald-950/50 border-emerald-500/30' };
  if (mode === 'HYBRID') return { label: 'Hybrid', className: 'text-sky-400 bg-sky-950/50 border-sky-500/30' };
  return { label: 'Cloud starter', className: 'text-amber-400 bg-amber-950/50 border-amber-500/30' };
}

export function LandingFounderSpotlight() {
  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/20 bg-gradient-to-b from-violet-950/25 to-zinc-950/80">
      <div className="border-b border-violet-500/15 px-4 py-4 sm:px-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-violet-400">Build without the cloud bill</p>
        <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">
          Founder OS — control plane on the web, compute on your machine
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Not one signup flow — pick an infrastructure path. Sovereign founders keep memory in Founder Vault and run
          Ollama locally. BYO Cloud connects what you already pay for. Free Starter gets you live in minutes on Render.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href="/founder-den?onboard=sovereign"
            className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500"
          >
            Start Sovereign path
          </Link>
          <Link
            href="/founder-node"
            className="rounded-lg border border-violet-500/40 px-4 py-2 text-xs font-semibold text-violet-200 hover:border-violet-400"
          >
            Download Founder Node
          </Link>
          <Link
            href="/settings/builder"
            className="rounded-lg border border-zinc-700 px-4 py-2 text-xs font-semibold text-zinc-300 hover:border-zinc-500"
          >
            Connect AI providers
          </Link>
        </div>
      </div>

      <div className="grid gap-2 p-3 sm:grid-cols-2 sm:p-4 lg:grid-cols-3 xl:grid-cols-5">
        {ONBOARDING_PATHS.map((path) => {
          const badge = computeBadge(path.computePlane);
          return (
            <Link
              key={path.id}
              href={PATH_LINKS[path.id] ?? '/founder-den'}
              className="group flex flex-col rounded-xl border border-zinc-800/80 bg-black/30 p-3 transition hover:border-violet-500/40 hover:bg-violet-950/20"
            >
              <span className="text-xl" aria-hidden>
                {path.icon}
              </span>
              <p className="mt-2 text-sm font-bold text-white group-hover:text-violet-50">{path.title}</p>
              <p className="mt-1 flex-1 text-[10px] leading-relaxed text-zinc-500">{path.tagline}</p>
              <div className="mt-3 space-y-1 border-t border-zinc-800/60 pt-2 text-[9px] text-zinc-500">
                <p>
                  <span className="text-zinc-600">Memory · </span>
                  {path.topology.memory}
                </p>
                <p>
                  <span className="text-zinc-600">Compute · </span>
                  {path.topology.compute}
                </p>
              </div>
              <span
                className={`mt-2 inline-flex w-fit rounded-md border px-1.5 py-0.5 text-[8px] font-bold uppercase ${badge.className}`}
              >
                {badge.label}
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

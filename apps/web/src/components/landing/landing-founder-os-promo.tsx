'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { fetchPublicFounderPromo } from '@/lib/api';

type Promo = { enabled: boolean; message: string | null; windowDays?: number };

const WHY_BULLETS = [
  { icon: '💻', text: 'Your PC does the heavy lifting.' },
  { icon: '🧠', text: 'Local AI with Ollama by default.' },
  { icon: '☁️', text: 'Cloud AI is optional, not required.' },
  { icon: '🔑', text: 'Bring your own API keys.' },
  { icon: '💰', text: 'Control your AI costs from day one.' },
  { icon: '🚀', text: 'Go from idea to prototype without infrastructure overhead.' },
];

const PATHS = [
  {
    id: 'sovereign',
    name: 'Sovereign',
    blurb: 'Everything runs locally. Maximum privacy. Lowest cost.',
    href: '/founder-den?onboard=sovereign',
  },
  {
    id: 'byo',
    name: 'Hybrid',
    blurb: 'Local compute with optional cloud models when needed.',
    href: '/founder-den?onboard=byo',
  },
  {
    id: 'founder_cloud',
    name: 'Founder Cloud',
    blurb: 'Scale to cloud infrastructure once your idea has traction.',
    href: '/founder-den?onboard=founder_cloud',
  },
];

export function LandingFounderOsPromo() {
  const [promo, setPromo] = useState<Promo | null>(null);

  useEffect(() => {
    fetchPublicFounderPromo()
      .then(setPromo)
      .catch(() => setPromo(null));
  }, []);

  const offerDays = promo?.windowDays && promo.windowDays > 0 ? promo.windowDays : 90;
  const offerActive = promo?.enabled === true;

  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/30 via-[#0a0a12] to-black shadow-[0_0_60px_rgba(139,92,246,0.08)]">
      {/* Headline + pitch */}
      <div className="border-b border-violet-500/15 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.28em] text-violet-400">Founder OS</p>
        <h2 className="mt-2 text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
          Your laptop is the compute.
        </h2>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-300 sm:text-base">
          Stop paying for cloud AI before you know your idea is worth building.
        </p>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Founder OS lets you brainstorm, research, plan, prototype, and experiment using your own
          hardware first. Connect local models with Ollama, bring your own AI providers when you need
          them, and only spend on cloud inference where it actually adds value.
        </p>
      </div>

      {/* Why founders use Founder OS */}
      <div className="border-b border-violet-500/15 px-4 py-5 sm:px-6 lg:px-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300/80">
          Why founders use Founder OS
        </p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {WHY_BULLETS.map((b) => (
            <li
              key={b.text}
              className="flex items-start gap-2 rounded-lg border border-zinc-800/70 bg-black/30 px-3 py-2 text-[12px] text-zinc-200"
            >
              <span className="text-base leading-none">{b.icon}</span>
              <span>{b.text}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Pick your path */}
      <div className="border-b border-violet-500/15 px-4 py-5 sm:px-6 lg:px-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300/80">Pick your path</p>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {PATHS.map((p) => (
            <Link
              key={p.id}
              href={p.href}
              className="group rounded-xl border border-zinc-800/80 bg-black/40 p-3 transition hover:border-violet-400/50 hover:bg-violet-950/20"
            >
              <p className="text-sm font-bold text-white group-hover:text-violet-100">{p.name}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">{p.blurb}</p>
              <p className="mt-2 text-[11px] font-semibold text-violet-300/90 group-hover:text-violet-200">
                Explore path →
              </p>
            </Link>
          ))}
        </div>
      </div>

      {/* Start building before you start spending */}
      <div className="border-b border-violet-500/15 px-4 py-4 sm:px-6 lg:px-8">
        <p className="text-sm font-semibold text-white">
          Start building before you start spending.
        </p>
        <p className="mt-1 text-[12px] text-zinc-400">
          Most founders spend money before they validate an idea. Founder OS helps you validate first.
          Then scale.
        </p>
      </div>

      {/* Two offers */}
      <div className="grid gap-3 px-4 py-5 sm:px-6 lg:grid-cols-2 lg:px-8">
        {/* GLM 5.2 limited-time offer */}
        {offerActive ? (
          <div className="rounded-xl border border-amber-500/40 bg-gradient-to-br from-amber-950/30 via-black/40 to-black p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-amber-300">
                Limited-Time Offer
              </p>
              <span className="rounded-full border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-amber-200">
                {offerDays} days free
              </span>
            </div>
            <p className="mt-2 text-base font-bold text-white">GLM 5.2 — Free for {offerDays} Days</p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-amber-100/80">
              {promo?.message ??
                'Every new account receives three months of complimentary access to GLM 5.2. Experiment, build, and validate your ideas before deciding what AI stack to invest in.'}
            </p>
            <Link
              href="/founder-den?onboard=sovereign"
              className="mt-3 inline-flex rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-1.5 text-xs font-bold text-amber-200 transition hover:bg-amber-500/20"
            >
              Start Founder OS →
            </Link>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-zinc-700/60 bg-black/30 p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-zinc-500">
              Limited-Time Offer
            </p>
            <p className="mt-2 text-base font-bold text-zinc-300">GLM 5.2 — Free for {offerDays} Days</p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-zinc-500">
              Every new account receives complimentary access to GLM 5.2. Experiment, build, and validate
              your ideas before deciding what AI stack to invest in.
            </p>
            <Link
              href="/founder-den?onboard=sovereign"
              className="mt-3 inline-flex rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-xs font-bold text-violet-200 transition hover:bg-violet-500/20"
            >
              Start Founder OS →
            </Link>
          </div>
        )}

        {/* Hire an AI Agent — Free (DDollar) */}
        <div className="rounded-xl border border-emerald-500/40 bg-gradient-to-br from-emerald-950/30 via-black/40 to-black p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300">
              Hire an AI Agent
            </p>
            <span className="rounded-full border border-emerald-500/50 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-emerald-200">
              Free*
            </span>
          </div>
          <p className="mt-2 text-base font-bold text-white">Hire an AI Agent — Free</p>
          <p className="mt-1.5 text-[12px] leading-relaxed text-zinc-300">
            Launch a ready-to-use agent and let it work for you. For trading research, connect your
            Bitfinex API and run paper trading to evaluate strategies with live market data — without
            risking real capital.
          </p>
          <p className="mt-2 text-[11px] font-semibold text-emerald-300/90">
            * Free in DDollar — earn it on-platform, no card needed.
          </p>
          <Link
            href="/agent-hub"
            className="mt-3 inline-flex rounded-lg border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-xs font-bold text-emerald-200 transition hover:bg-emerald-500/20"
          >
            Hire Agent →
          </Link>
        </div>
      </div>
    </section>
  );
}

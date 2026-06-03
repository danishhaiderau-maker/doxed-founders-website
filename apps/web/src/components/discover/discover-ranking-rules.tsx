'use client';

import Link from 'next/link';
import {
  DISCOVER_ACTIVITY_FACTORS,
  DISCOVER_BUBBLE_SIZE_TIERS,
  DISCOVER_RANKING_RULES_HEADLINE,
  DISCOVER_RANKING_RULES_INTRO,
  DISCOVER_RING_LEGEND_NOTE,
  DISCOVER_UNIVERSE_COLORS,
  type DiscoverUniverseStage,
} from '@dcf/utils';

const STAGE_ORDER: DiscoverUniverseStage[] = [
  'building',
  'validation',
  'live',
  'recently_listed',
];

export function DiscoverRankingRules() {
  return (
    <section
      aria-label="Discover ranking rules"
      className="rounded-xl border border-zinc-700/50 bg-zinc-950/40 px-4 py-4 backdrop-blur-sm sm:px-5 sm:py-5"
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">
        Ranking rules
      </p>
      <h2 className="mt-1 text-base font-bold text-white sm:text-lg">
        {DISCOVER_RANKING_RULES_HEADLINE}
      </h2>
      <p className="mt-2 max-w-4xl text-sm leading-relaxed text-zinc-400">
        {DISCOVER_RANKING_RULES_INTRO}
      </p>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-zinc-800/60 bg-black/25 p-3">
          <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">
            When bubbles get bigger
          </p>
          <ul className="mt-2 space-y-1.5 text-xs text-zinc-300">
            {DISCOVER_BUBBLE_SIZE_TIERS.map((t) => (
              <li key={t.label}>
                <span className="font-semibold text-white">{t.label}</span>
                <span className="text-zinc-500"> — activity score ≥ {t.minScore} → </span>
                {t.diameterPx}px diameter
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-zinc-500">
            Post updates, ship GitHub commits, earn DDollar inflow & volume, gain followers, scout
            stakes, and community threads — each capped so scores stay fair.
          </p>
        </div>

        <div className="rounded-lg border border-zinc-800/60 bg-black/25 p-3">
          <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">
            Outer ring (stage)
          </p>
          <p className="mt-1 text-[11px] text-zinc-500">{DISCOVER_RING_LEGEND_NOTE}</p>
          <ul className="mt-3 space-y-2">
            {STAGE_ORDER.map((key) => {
              const meta = DISCOVER_UNIVERSE_COLORS[key];
              return (
                <li key={key} className="flex items-center gap-2 text-xs text-zinc-300">
                  <span
                    className="h-4 w-4 shrink-0 rounded-full border-[3px] bg-zinc-950/80"
                    style={{ borderColor: meta.border }}
                    aria-hidden
                  />
                  <span className="font-medium text-white">{meta.label}</span>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="rounded-lg border border-zinc-800/60 bg-black/25 p-3">
          <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">
            Activity score factors (max ~100 pts)
          </p>
          <ul className="mt-2 max-h-[140px] space-y-1 overflow-y-auto text-[11px] text-zinc-400">
            {DISCOVER_ACTIVITY_FACTORS.map((f) => (
              <li key={f.key}>
                <span className="text-zinc-200">{f.label}</span>
                <span className="text-emerald-400/90"> — up to {f.maxPoints} pts</span>
              </li>
            ))}
          </ul>
          <Link
            href="/founder-den"
            className="mt-3 inline-block text-xs font-semibold text-violet-300 hover:text-violet-200"
          >
            Grow your score in Mission Control →
          </Link>
        </div>
      </div>
    </section>
  );
}

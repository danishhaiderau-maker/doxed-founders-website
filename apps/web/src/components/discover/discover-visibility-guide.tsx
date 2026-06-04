'use client';

import Link from 'next/link';
import {
  DISCOVER_ACTIVITY_FACTORS,
  DISCOVER_BUBBLE_SCORE_FORMULA,
  DISCOVER_BUBBLE_SIZE_TIERS,
  DISCOVER_RING_LEGEND_NOTE,
  DISCOVER_UNIVERSE_COLORS,
  DISCOVER_VISIBILITY_SUMMARY,
  type DiscoverUniverseStage,
} from '@dcf/utils';

export function DiscoverVisibilityGuide() {
  return (
    <section
      aria-label="How Discover visibility works"
      className="mt-10 rounded-2xl border border-blue-500/20 bg-blue-950/10 p-5 sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-blue-400">
            For founders
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">How bubbles grow & visibility increases</h2>
          <p className="mt-2 text-sm leading-relaxed text-zinc-400">{DISCOVER_VISIBILITY_SUMMARY}</p>
          <p className="mt-2 text-xs text-zinc-500">
            On{' '}
            <Link href="/discover" className="text-blue-300 underline-offset-2 hover:underline">
              Discover
            </Link>
            , bubble <strong className="font-semibold text-zinc-300">size</strong> follows your{' '}
            <strong className="font-semibold text-zinc-300">activity score</strong> (badge number).
            Sort order and sidebar slots favor projects with real DDollar flow, GitHub proof, and
            community signals in the timeframe you pick (1h–7d).
          </p>
          <p className="mt-2 text-xs text-zinc-500">{DISCOVER_RING_LEGEND_NOTE}</p>
          <ul className="mt-2 flex flex-wrap gap-3">
            {(['building', 'validation', 'live'] as DiscoverUniverseStage[]).map((key) => {
              const meta = DISCOVER_UNIVERSE_COLORS[key];
              return (
                <li key={key} className="flex items-center gap-1.5 text-[11px] text-zinc-400">
                  <span
                    className="h-3 w-3 rounded-full border-2"
                    style={{ borderColor: meta.border }}
                  />
                  {meta.label}
                </li>
              );
            })}
          </ul>
        </div>
        <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-4 py-3 text-xs text-zinc-400">
          <p className="font-semibold uppercase tracking-wider text-zinc-500">Bubble size tiers</p>
          <ul className="mt-2 space-y-1">
            {DISCOVER_BUBBLE_SIZE_TIERS.map((t) => (
              <li key={t.label}>
                <span className="text-white">{t.label}</span> — score ≥ {t.minScore} → {t.diameterPx}
                px
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-4 text-[11px] font-medium text-zinc-500">{DISCOVER_BUBBLE_SCORE_FORMULA}</p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {DISCOVER_ACTIVITY_FACTORS.map((f) => (
          <div
            key={f.key}
            className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-3"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-white">{f.label}</p>
              <span className="shrink-0 rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold text-emerald-400">
                up to {f.maxPoints} pts
              </span>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-500">{f.description}</p>
            <p className="mt-2 text-[11px] font-medium text-blue-200/90">→ {f.founderAction}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <Link
          href="/founder-den"
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
        >
          Open Mission Control
        </Link>
        <Link
          href="/list-your-project"
          className="rounded-lg border border-zinc-600 px-4 py-2 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
        >
          Apply for listing
        </Link>
      </div>
    </section>
  );
}

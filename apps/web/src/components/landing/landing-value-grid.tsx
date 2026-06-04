'use client';

import { LandingFunFactBar } from '@/components/landing/landing-fun-fact-bar';

const GRID_ALT =
  'Curated builders in public. Conviction over hype. Founder OS for shipping. Founder Node vault + bring your own keys. Fund founders who deliver.';

/**
 * Four-panel infographic — serves 1x/2x/3x assets so large displays stay sharp (no upscaled 1024px stretch).
 */
export function LandingValueGrid() {
  return (
    <div className="space-y-3">
      <section
        aria-label="Platform value proposition"
        className="w-full overflow-hidden rounded-2xl border border-emerald-500/25 shadow-2xl shadow-emerald-950/30"
      >
        <div className="relative aspect-[16/8.5] w-full sm:aspect-[2/1]">
          {/* Native srcSet — avoids Next image optimizer downscaling UHD assets */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/images/landing-value-grid-2560.webp"
            srcSet="/images/landing-value-grid-1536.webp 1536w, /images/landing-value-grid-2560.webp 2560w, /images/landing-value-grid-3840.webp 3840w"
            sizes="(min-width: 1920px) min(100vw, 3840px), (min-width: 1280px) min(100vw, 2560px), 100vw"
            alt={GRID_ALT}
            width={2560}
            height={1707}
            decoding="async"
            fetchPriority="high"
            className="absolute inset-0 h-full w-full object-cover object-top"
          />
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 border-t border-emerald-500/20 bg-black/60 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400">
          <span className="flex items-center gap-1.5">
            <span className="text-emerald-400" aria-hidden>
              👥
            </span>
            Build in public
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-emerald-400" aria-hidden>
              📊
            </span>
            <span className="text-emerald-300">Validate demand</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-emerald-400" aria-hidden>
              🛡
            </span>
            Launch with trust
          </span>
        </div>
      </section>
      <LandingFunFactBar />
    </div>
  );
}

'use client';

import Image from 'next/image';
import { LandingFunFactBar } from '@/components/landing/landing-fun-fact-bar';

/** Four-panel infographic (image 1, top crop) + interactive fun fact with linked sources. */
export function LandingValueGrid() {
  return (
    <div className="space-y-3">
      <section aria-label="Platform value proposition" className="w-full overflow-hidden rounded-2xl border border-emerald-500/25 shadow-2xl shadow-emerald-950/30">
        {/* Crop bottom fun-fact row from PNG — replaced by LandingFunFactBar */}
        <div className="relative aspect-[16/10] w-full sm:aspect-[16/9]">
          <Image
            src="/images/landing-value-grid-primary.png"
            alt="Private by default, public by proof. Show your face. Trust through execution. Trade builders, not hype."
            fill
            className="object-cover object-top"
            priority
            sizes="(max-width: 88rem) 100vw, 88rem"
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

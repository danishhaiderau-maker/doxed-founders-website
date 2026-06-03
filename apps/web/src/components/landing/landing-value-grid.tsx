'use client';

import Image from 'next/image';

/** Full-width value grid — matches approved landing infographic (image 1). */
export function LandingValueGrid() {
  return (
    <section aria-label="Platform value proposition" className="w-full">
      <Image
        src="/images/landing-value-grid-primary.png"
        alt="Private by default, public by proof. Show your face. Trust through execution. Trade builders, not hype. Pump.fun trader statistics and sources."
        width={1920}
        height={1280}
        className="h-auto w-full rounded-2xl border border-emerald-500/25 shadow-2xl shadow-emerald-950/30"
        priority
        sizes="(max-width: 88rem) 100vw, 88rem"
      />
    </section>
  );
}

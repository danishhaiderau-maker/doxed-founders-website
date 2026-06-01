'use client';

import { useEffect, useState } from 'react';
import { fetchPlatformStats, PlatformStats } from '@/lib/api';
import { LandingHeader, LandingSinglePage } from '@/components/landing/landing-mockup-sections';

export function LandingPage() {
  const [platformStats, setPlatformStats] = useState<PlatformStats | null>(null);

  useEffect(() => {
    fetchPlatformStats().then(setPlatformStats).catch(() => setPlatformStats(null));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <LandingHeader />
      <LandingSinglePage stats={platformStats} />
    </main>
  );
}

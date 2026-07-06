'use client';

import { useEffect } from 'react';

export default function FounderNodeRedirectPage() {
  useEffect(() => {
    window.location.replace('/downloads#founder-node');
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#050508] text-white">
      <p className="text-sm text-zinc-400">Redirecting to Downloads…</p>
    </main>
  );
}

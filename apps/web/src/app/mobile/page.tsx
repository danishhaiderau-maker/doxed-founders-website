'use client';

import { useEffect } from 'react';

export default function MobileRedirectPage() {
  useEffect(() => {
    window.location.replace('/downloads#mobile');
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#050508] text-white">
      <p className="text-sm text-zinc-400">Redirecting to Downloads…</p>
    </main>
  );
}

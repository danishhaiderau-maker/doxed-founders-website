'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { fetchPublicFounderPromo } from '@/lib/api';

type Props = {
  className?: string;
};

export function FounderPromoSignupBanner({ className = '' }: Props) {
  const [promo, setPromo] = useState<{
    enabled: boolean;
  } | null>(null);

  useEffect(() => {
    fetchPublicFounderPromo()
      .then(setPromo)
      .catch(() => setPromo(null));
  }, []);

  if (!promo?.enabled) return null;

  return (
    <div
      className={`rounded-lg border border-amber-500/35 bg-amber-950/25 px-4 py-3 ${className}`}
    >
      <p className="text-xs font-semibold text-amber-200">
        Founder Free - managed AI quota included
      </p>
      <p className="mt-1 text-xs text-amber-100/90">
        Ask questions, plan work, and make small edits before upgrading. Use personal keys or local models at any time.
      </p>
      <Link
        href="/founder-den?onboard=byo"
        className="mt-2 inline-flex text-xs font-semibold text-violet-200 underline hover:text-white"
      >
        Set up Founder workspace
      </Link>
    </div>
  );
}

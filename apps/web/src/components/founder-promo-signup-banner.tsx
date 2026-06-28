'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { fetchPublicFounderPromo } from '@/lib/api';

type Props = {
  className?: string;
};

export function FounderPromoSignupBanner({ className = '' }: Props) {
  const [promo, setPromo] = useState<{ enabled: boolean; message: string | null } | null>(null);

  useEffect(() => {
    fetchPublicFounderPromo()
      .then(setPromo)
      .catch(() => setPromo(null));
  }, []);

  if (!promo?.enabled || !promo.message) return null;

  return (
    <div
      className={`rounded-lg border border-amber-500/35 bg-gradient-to-r from-amber-950/40 to-violet-950/30 px-4 py-3 ${className}`}
    >
      <p className="text-xs font-semibold text-amber-200">Founder OS — 3 months free AI for all new accounts</p>
      <p className="mt-1 text-xs text-amber-100/90">{promo.message}</p>
      <Link
        href="/founder-den?onboard=byo"
        className="mt-2 inline-flex text-xs font-semibold text-violet-200 underline hover:text-white"
      >
        Start Founder OS setup →
      </Link>
    </div>
  );
}

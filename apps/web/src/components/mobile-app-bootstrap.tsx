'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { isMobileAppQuery, persistMobileAppMode } from '@/lib/mobile-app-mode';

/** Persists Android app mode when opened with ?app=android (APK entry URL). */
export function MobileAppBootstrap() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams?.toString() ?? '';
    if (isMobileAppQuery(qs ? `?${qs}` : '')) {
      persistMobileAppMode();
    }
  }, [searchParams]);

  return null;
}

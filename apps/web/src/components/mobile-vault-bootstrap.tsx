'use client';

import { useEffect } from 'react';
import { readMobileAppMode } from '@/lib/mobile-app-mode';
import { initMobileVaultService } from '@/lib/mobile-vault/service';

/** Starts background vault sync when running inside the Android APK. */
export function MobileVaultBootstrap() {
  useEffect(() => {
    if (!readMobileAppMode()) return;
    void initMobileVaultService();
  }, []);

  return null;
}

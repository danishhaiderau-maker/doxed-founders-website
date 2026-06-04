'use client';

import { useEffect, useState } from 'react';
import { readMobileAppMode } from '@/lib/mobile-app-mode';

export function useMobileAppMode(): boolean {
  const [active, setActive] = useState(false);

  useEffect(() => {
    setActive(readMobileAppMode());
  }, []);

  return active;
}

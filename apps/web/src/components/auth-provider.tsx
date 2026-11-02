'use client';

import { SessionProvider } from 'next-auth/react';
import { isBackendApiRetired } from '@/lib/backend-api-retired';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const retired = isBackendApiRetired();
  return (
    <SessionProvider
      // No session refetch loops against a dead Nest/Railway stack (t781u).
      refetchInterval={retired ? 0 : undefined}
      refetchOnWindowFocus={!retired}
      refetchWhenOffline={false}
    >
      {children}
    </SessionProvider>
  );
}

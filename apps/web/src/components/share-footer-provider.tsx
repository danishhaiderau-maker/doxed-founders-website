'use client';

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { PLATFORM_X_SHARE_FOOTER } from '@dcf/utils';
import { fetchGlobalShareFooter } from '@/lib/api';

type ShareFooterContextValue = {
  footer: string;
  reload: () => void;
};

const ShareFooterContext = createContext<ShareFooterContextValue>({
  footer: PLATFORM_X_SHARE_FOOTER,
  reload: () => {},
});

export function ShareFooterProvider({ children }: { children: React.ReactNode }) {
  const [footer, setFooter] = useState(PLATFORM_X_SHARE_FOOTER);

  const load = useCallback(async () => {
    try {
      const res = await fetchGlobalShareFooter();
      if (res.footer?.trim()) setFooter(res.footer.trim());
    } catch {
      setFooter(PLATFORM_X_SHARE_FOOTER);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <ShareFooterContext.Provider value={{ footer, reload: load }}>
      {children}
    </ShareFooterContext.Provider>
  );
}

export function useShareFooter() {
  return useContext(ShareFooterContext).footer;
}

export function useShareFooterActions() {
  return useContext(ShareFooterContext);
}

'use client';

import { useCallback, useEffect, useState } from 'react';

/** Background UI refresh when relay sim is idle (sim running, live view off). */
export const AGENT_HUB_POLL_SIM_MS = 20_000;
/** Faster UI refresh when relay sim + Live view toggle (display only). */
export const AGENT_HUB_POLL_SIM_LIVE_VIEW_MS = 8_000;
export const AGENT_HUB_POLL_BOT_MS = 45_000;
export const AGENT_HUB_POLL_IDLE_MS = 90_000;

const STORAGE_PREFIX = 'dcf-relay-sim-live-view';

function storageKey(userId: string) {
  return `${STORAGE_PREFIX}:${userId}`;
}

/** Per signed-in user, stored in this browser only — not synced to server or other devices. */
export function useRelaySimLiveView(userId: string | undefined) {
  const key = storageKey(userId?.trim() || 'guest');
  const [enabled, setEnabledState] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      setEnabledState(localStorage.getItem(key) === '1');
    } catch {
      setEnabledState(false);
    }
    setHydrated(true);
  }, [key]);

  const setEnabled = useCallback(
    (next: boolean) => {
      setEnabledState(next);
      try {
        localStorage.setItem(key, next ? '1' : '0');
      } catch {
        /* ignore quota / private mode */
      }
    },
    [key],
  );

  return { liveViewEnabled: enabled, setLiveViewEnabled: setEnabled, hydrated };
}

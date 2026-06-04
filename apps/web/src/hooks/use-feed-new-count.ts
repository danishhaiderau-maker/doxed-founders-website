'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchFeedHub } from '@/lib/api';

const STORAGE_KEY = 'dcf-feed-last-seen-at';

export function markFeedSeen() {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, new Date().toISOString());
}

export function useFeedNewCount(pollMs = 60_000) {
  const [count, setCount] = useState(0);

  const load = useCallback(async () => {
    try {
      const hub = await fetchFeedHub('all', 'all', undefined, 80);
      const lastSeenRaw = typeof window !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;
      const lastSeen = lastSeenRaw ? new Date(lastSeenRaw).getTime() : 0;
      const fresh = hub.stream.filter((e) => new Date(e.at).getTime() > lastSeen).length;
      setCount(fresh);
    } catch {
      setCount(0);
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs]);

  return { count, refresh: load };
}

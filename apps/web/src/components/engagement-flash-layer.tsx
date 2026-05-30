'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { fetchEngagementFlashes, type EngagementFlash } from '@/lib/api';

const FLASH_MS = 3200;
const POLL_MS = 12_000;

type FlashListener = (flash: EngagementFlash) => void;
const listeners = new Set<FlashListener>();

/** Push an instant flash (e.g. after the user comments) without waiting for poll. */
export function pushEngagementFlash(
  flash: Omit<EngagementFlash, 'id' | 'at'> & Partial<Pick<EngagementFlash, 'id'>>,
) {
  const full: EngagementFlash = {
    id: flash.id ?? `local-${Date.now()}`,
    at: new Date().toISOString(),
    emoji: flash.emoji,
    message: flash.message,
    link: flash.link,
  };
  listeners.forEach((fn) => fn(full));
}

function heatBadgeClass(label: 'Blazing' | 'Heating up') {
  if (label === 'Blazing') return 'bg-orange-500/25 text-orange-200';
  return 'bg-violet-500/20 text-violet-200';
}

export { heatBadgeClass };

export function EngagementFlashLayer() {
  const [current, setCurrent] = useState<EngagementFlash | null>(null);
  const seenRef = useRef<Set<string>>(new Set());
  const queueRef = useRef<EngagementFlash[]>([]);
  const sinceRef = useRef(new Date().toISOString());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showingRef = useRef(false);

  useEffect(() => {
    function showNext() {
      if (showingRef.current) return;
      const next = queueRef.current.shift();
      if (!next) return;
      showingRef.current = true;
      setCurrent(next);
      timerRef.current = setTimeout(() => {
        setCurrent(null);
        showingRef.current = false;
        timerRef.current = setTimeout(showNext, 400);
      }, FLASH_MS);
    }

    function enqueue(flash: EngagementFlash) {
      if (seenRef.current.has(flash.id)) return;
      seenRef.current.add(flash.id);
      queueRef.current.push(flash);
      showNext();
    }

    const onLocal = (flash: EngagementFlash) => enqueue(flash);
    listeners.add(onLocal);

    async function poll() {
      try {
        const flashes = await fetchEngagementFlashes(sinceRef.current);
        sinceRef.current = new Date().toISOString();
        for (const flash of flashes) enqueue(flash);
      } catch {
        // ignore polling errors
      }
    }

    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      listeners.delete(onLocal);
      clearInterval(interval);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!current) return null;

  const inner = (
    <div className="pointer-events-auto flex max-w-md items-start gap-3 rounded-xl border border-indigo-400/40 bg-indigo-950/95 px-4 py-3 shadow-2xl shadow-indigo-900/40 backdrop-blur-md">
      <span className="text-xl">{current.emoji}</span>
      <p className="text-sm font-medium leading-snug text-white">{current.message}</p>
    </div>
  );

  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-[100] animate-in fade-in slide-in-from-bottom-4 duration-300">
      {current.link ? (
        <Link href={current.link} className="block transition hover:scale-[1.02]">
          {inner}
        </Link>
      ) : (
        inner
      )}
    </div>
  );
}

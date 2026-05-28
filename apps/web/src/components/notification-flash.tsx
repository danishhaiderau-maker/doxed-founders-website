'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppNotification, fetchNotifications } from '@/lib/api';

const SEEN_KEY = 'dcf-flash-notifications';
const FLASH_TYPES = new Set(['TRADER_WIN', 'TRADER_LOSS']);

type FlashToast = AppNotification & { expiresAt: number };

function loadSeen(): Set<string> {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = sessionStorage.getItem(SEEN_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function saveSeen(seen: Set<string>) {
  const trimmed = [...seen].slice(-80);
  sessionStorage.setItem(SEEN_KEY, JSON.stringify(trimmed));
}

export function NotificationFlashProvider({ children }: { children: React.ReactNode }) {
  const { data: session } = useSession();
  const [toasts, setToasts] = useState<FlashToast[]>([]);
  const seenRef = useRef<Set<string>>(loadSeen());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    if (!session?.accessToken) return;

    const poll = async () => {
      try {
        const items = await fetchNotifications(session.accessToken!);
        const fresh = items.filter(
          (n) =>
            FLASH_TYPES.has(n.type) &&
            !n.readAt &&
            !seenRef.current.has(n.id) &&
            Date.now() - new Date(n.createdAt).getTime() < 15 * 60 * 1000,
        );

        if (!fresh.length) return;

        for (const n of fresh) {
          seenRef.current.add(n.id);
        }
        saveSeen(seenRef.current);

        setToasts((prev) => {
          const existing = new Set(prev.map((t) => t.id));
          const next = fresh
            .filter((n) => !existing.has(n.id))
            .map((n) => ({ ...n, expiresAt: Date.now() + 12_000 }));
          return [...next, ...prev].slice(0, 4);
        });
      } catch {
        /* ignore poll errors */
      }
    };

    poll();
    const interval = setInterval(poll, 20_000);
    return () => clearInterval(interval);
  }, [session?.accessToken]);

  useEffect(() => {
    if (!toasts.length) return;
    const timers = toasts.map((t) =>
      setTimeout(() => dismiss(t.id), Math.max(0, t.expiresAt - Date.now())),
    );
    return () => timers.forEach(clearTimeout);
  }, [toasts, dismiss]);

  return (
    <>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 top-0 z-[100] flex flex-col items-center gap-2 p-4 sm:items-end sm:pr-6"
        aria-live="polite"
      >
        {toasts.map((toast) => {
          const isWin = toast.type === 'TRADER_WIN';
          return (
            <div
              key={toast.id}
              className={`pointer-events-auto w-full max-w-md animate-flash-in rounded-xl border p-4 shadow-2xl backdrop-blur-md ${
                isWin
                  ? 'flash-win border-emerald-400/50 bg-emerald-950/90'
                  : 'flash-loss border-red-500/50 bg-red-950/90'
              }`}
            >
              <div className="flex items-start gap-3">
                <span
                  className={`text-2xl ${isWin ? 'animate-bounce-soft' : 'animate-shake-soft'}`}
                  aria-hidden
                >
                  {isWin ? '🚀' : '📉'}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-white">{toast.title}</p>
                  <p className="mt-1 text-sm text-zinc-300">{toast.body}</p>
                  {toast.link && (
                    <Link
                      href={toast.link}
                      className={`mt-3 inline-block text-sm font-medium underline-offset-2 hover:underline ${
                        isWin ? 'text-emerald-300' : 'text-red-300'
                      }`}
                    >
                      View position →
                    </Link>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => dismiss(toast.id)}
                  className="text-zinc-400 hover:text-white"
                  aria-label="Dismiss"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

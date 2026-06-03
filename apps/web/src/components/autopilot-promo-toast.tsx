'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

const DISMISS_KEY = 'dcf-autopilot-promo-dismissed-at';
const SHOW_INTERVAL_MS = 6 * 60 * 60 * 1000;

type AutopilotPromoToastProps = {
  show: boolean;
  autopilotEnabled: boolean;
  pendingPublishCount?: number;
  onEnable?: () => void;
};

export function AutopilotPromoToast({
  show,
  autopilotEnabled,
  pendingPublishCount = 0,
  onEnable,
}: AutopilotPromoToastProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!show || autopilotEnabled) {
      setVisible(false);
      return;
    }
    try {
      const raw = localStorage.getItem(DISMISS_KEY);
      const last = raw ? Number(raw) : 0;
      if (Date.now() - last < SHOW_INTERVAL_MS) return;
    } catch {
      /* ignore */
    }
    const t = window.setTimeout(() => setVisible(true), 2400);
    return () => window.clearTimeout(t);
  }, [show, autopilotEnabled]);

  useEffect(() => {
    if (!visible) return;
    const t = window.setTimeout(() => setVisible(false), 12_000);
    return () => window.clearTimeout(t);
  }, [visible]);

  if (!visible) return null;

  function dismiss() {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
    setVisible(false);
  }

  return (
    <div
      role="status"
      className="pointer-events-auto fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-emerald-500/40 bg-emerald-950/95 p-4 shadow-2xl shadow-black/50 transition-opacity"
    >
      <button
        type="button"
        onClick={dismiss}
        className="absolute right-2 top-2 text-xs text-zinc-500 hover:text-white"
        aria-label="Dismiss"
      >
        ✕
      </button>
      <p className="pr-6 text-sm font-semibold text-emerald-100">Turn on Autopilot?</p>
      <p className="mt-1 text-xs leading-relaxed text-emerald-200/80">
        Sync GitHub, publish build updates, and redeploy Vercel + Railway when connected.
        {pendingPublishCount > 0
          ? ` You have ${pendingPublishCount} update(s) ready to ship.`
          : ''}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {onEnable ? (
          <button
            type="button"
            onClick={() => {
              onEnable();
              dismiss();
            }}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
          >
            Enable Autopilot
          </button>
        ) : null}
        <Link
          href={AI_STACK_HREF}
          onClick={dismiss}
          className="rounded-lg border border-emerald-600/40 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-900/40"
        >
          AI Stack
        </Link>
      </div>
    </div>
  );
}

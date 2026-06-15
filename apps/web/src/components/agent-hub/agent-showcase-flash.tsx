'use client';

import type { AgentShowcaseFlash } from '@dcf/utils';
import { formatRelativeTime } from '@dcf/utils';

const TONE_STYLES = {
  'new-session': {
    wrap: 'border-cyan-400/45 bg-gradient-to-r from-cyan-950/55 via-emerald-950/35 to-zinc-950/50 shadow-lg shadow-cyan-950/25',
    badge: 'bg-cyan-500/25 text-cyan-100 ring-cyan-400/40',
  },
  'live-testing': {
    wrap: 'border-violet-500/35 bg-gradient-to-r from-violet-950/45 via-zinc-950/40 to-emerald-950/25',
    badge: 'bg-violet-500/20 text-violet-100 ring-violet-400/35',
  },
  offline: {
    wrap: 'border-amber-500/35 bg-gradient-to-r from-amber-950/40 to-zinc-950/50',
    badge: 'bg-amber-500/20 text-amber-100 ring-amber-400/35',
  },
} as const;

/** Explains why the admin showcase reset — builds trust that strategy is actively tested. */
export function AgentShowcaseFlashBanner({
  flash,
  className = 'mx-4 mb-4 sm:mx-6',
}: {
  flash: AgentShowcaseFlash | null;
  className?: string;
}) {
  if (!flash) return null;

  const style = TONE_STYLES[flash.tone];

  return (
    <div
      className={`overflow-hidden rounded-2xl border px-5 py-4 ${className} ${style.wrap}`}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300/90">
              Admin research update
            </p>
            {flash.botVersion && (
              <span
                className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${style.badge}`}
              >
                {flash.botVersion}
              </span>
            )}
            {flash.freshCollectionMode && (
              <span className="rounded-full bg-blue-500/20 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-blue-200 ring-1 ring-blue-400/35">
                Fresh $500 run
              </span>
            )}
          </div>
          <p className="mt-2 text-base font-bold text-white">{flash.headline}</p>
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-zinc-300">{flash.body}</p>
          {flash.sessionStartedAt && (
            <p className="mt-2 text-xs text-zinc-500">
              Session started {formatRelativeTime(flash.sessionStartedAt)} · showcase stats below are from this run
              only
            </p>
          )}
        </div>
        {flash.tone === 'new-session' && (
          <span className="shrink-0 rounded-full bg-emerald-500/20 px-3 py-1 text-[10px] font-bold uppercase tracking-wide text-emerald-200 ring-1 ring-emerald-400/40">
            Live testing
          </span>
        )}
      </div>
    </div>
  );
}

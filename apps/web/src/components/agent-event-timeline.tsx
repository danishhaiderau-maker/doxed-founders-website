'use client';

import { useMemo } from 'react';
import { useFounderEvents, type FounderEvent } from '@/lib/founder-event-bus';

const CATEGORY_COLOR: Record<string, string> = {
  AI: 'text-violet-300 bg-violet-500/10 border-violet-500/30',
  GIT: 'text-orange-300 bg-orange-500/10 border-orange-500/30',
  DEPLOY: 'text-sky-300 bg-sky-500/10 border-sky-500/30',
  AGENT: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  FILE: 'text-zinc-300 bg-zinc-500/10 border-zinc-500/30',
  TERMINAL: 'text-zinc-300 bg-zinc-500/10 border-zinc-500/30',
  CURSOR: 'text-blue-300 bg-blue-500/10 border-blue-500/30',
  GITHUB: 'text-zinc-200 bg-zinc-500/10 border-zinc-500/30',
  BUILD: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
  VOICE: 'text-pink-300 bg-pink-500/10 border-pink-500/30',
  VAULT: 'text-teal-300 bg-teal-500/10 border-teal-500/30',
  SYSTEM: 'text-zinc-400 bg-zinc-700/20 border-zinc-700',
};

const LEVEL_DOT: Record<string, string> = {
  info: 'bg-zinc-500',
  success: 'bg-emerald-400',
  warn: 'bg-amber-400',
  error: 'bg-red-400',
};

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

export function AgentEventTimeline({ stream }: { stream?: string }) {
  const events = useFounderEvents(stream ? { stream } : undefined);

  const ordered = useMemo(() => [...events].reverse().slice(0, 60), [events]);

  if (ordered.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-[11px] text-zinc-600">
        No live events yet. Send a message to Founder Brain or open an agent to see real execution stages stream here.
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {ordered.map((ev) => (
        <EventRow key={ev.id} ev={ev} />
      ))}
    </div>
  );
}

function EventRow({ ev }: { ev: FounderEvent }) {
  const cat = ev.category;
  return (
    <div className="group flex items-start gap-2 border-b border-zinc-900/70 px-3 py-2 hover:bg-zinc-900/40">
      <span className={`mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full ${LEVEL_DOT[ev.level]}`} />
      <span className="mt-0.5 shrink-0 font-mono text-[10px] text-zinc-600">{fmtTime(ev.ts)}</span>
      <span
        className={`mt-0.5 shrink-0 rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide ${
          CATEGORY_COLOR[cat] ?? CATEGORY_COLOR.SYSTEM
        }`}
      >
        {cat}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs text-zinc-200">{ev.message}</p>
        {typeof ev.progress === 'number' && (
          <div className="mt-1 h-1 w-full max-w-[200px] overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full rounded-full bg-violet-500 transition-all duration-500"
              style={{ width: `${Math.round(ev.progress * 100)}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function EventStreamSummary({ stream }: { stream: string }) {
  const events = useFounderEvents({ stream });
  const latest = events[events.length - 1];
  if (!latest) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${LEVEL_DOT[latest.level]} animate-pulse`} />
      <span className="truncate text-[11px] text-zinc-300">{latest.message}</span>
    </div>
  );
}

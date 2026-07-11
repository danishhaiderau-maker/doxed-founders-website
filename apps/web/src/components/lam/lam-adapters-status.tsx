'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import type { LamAdapterStatus } from './types';

type Props = { accessToken: string };

/**
 * LamAdaptersStatus — shows which LAM adapters are available.
 *
 * Browser is always available (capped by DDollar). Computer-Use is the
 * premium tier, gated to Doxxed Builders + the COMPUTER_USE_ENABLED
 * server flag. Renders a compact pill row so it can drop into the
 * Founder OS shell's Agents tab header.
 */
export function LamAdaptersStatus({ accessToken }: Props) {
  const [adapters, setAdapters] = useState<LamAdapterStatus[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/lam/adapters'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) setAdapters((await res.json()) as LamAdapterStatus[]);
    } catch {
      // surfaced by empty state
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-zinc-600">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-zinc-500" />
        Checking adapters…
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {adapters.map((a) => {
        const isBrowser = a.id === 'browser';
        const on = a.available;
        const label = isBrowser ? 'Browser' : 'Computer Use';
        const icon = isBrowser ? '🌐' : '🖥️';
        return (
          <span
            key={a.id}
            title={a.reason ?? (on ? 'Available' : 'Unavailable')}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium ${
              on
                ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-300'
                : 'border-zinc-700 bg-zinc-900/40 text-zinc-500'
            }`}
          >
            <span>{icon}</span>
            {label}
            {a.premium && (
              <span className="ml-1 rounded bg-violet-950/60 px-1 text-[9px] uppercase text-violet-300">
                Doxxed
              </span>
            )}
            <span className={on ? 'text-emerald-400' : 'text-zinc-600'}>
              {on ? '●' : '○'}
            </span>
          </span>
        );
      })}
    </div>
  );
}

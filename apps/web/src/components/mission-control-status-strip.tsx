'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { fetchPlatformSyncStatus, type PlatformSyncStatus } from '@/lib/api';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

type Props = {
  accessToken: string;
  buildWorker?: string;
  onRefresh?: () => void;
};

const INFRA_KEYS = ['github', 'neon', 'vercel', 'railway'] as const;

export function MissionControlStatusStrip({ accessToken, buildWorker, onRefresh }: Props) {
  const [status, setStatus] = useState<PlatformSyncStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchPlatformSyncStatus(accessToken));
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const infra = status?.platforms.filter((p) =>
    INFRA_KEYS.includes(p.key as (typeof INFRA_KEYS)[number]),
  ) ?? [];
  const connected = infra.filter((p) => p.connected).length;
  const cp = status?.controlPlane;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-zinc-400">Infra</span>
        {infra.length === 0 ? (
          <span className="text-zinc-600">Loading…</span>
        ) : (
          infra.map((p) => (
            <span
              key={p.key}
              className={`rounded-full px-2 py-0.5 ${
                p.connected ? 'bg-emerald-950/50 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
              }`}
            >
              {p.label}
              {p.connected ? ' ✓' : ''}
            </span>
          ))
        )}
        {infra.length > 0 && (
          <span className="text-zinc-600">
            {connected}/{infra.length} connected
          </span>
        )}
        {cp && (
          <span className="text-zinc-600">
            · Ask {cp.legs.find((l) => l.key === 'ask')?.connected ? '✓' : '○'} · Code{' '}
            {cp.legs.find((l) => l.key === 'code')?.connected ? '✓' : '○'}
          </span>
        )}
        {buildWorker && buildWorker !== 'NONE' && (
          <span className="rounded-full bg-violet-950/50 px-2 py-0.5 text-violet-300">
            Builder Agent active
          </span>
        )}
        {status?.autopilotEnabled && (
          <span className="rounded-full bg-emerald-950/40 px-2 py-0.5 text-emerald-400">
            Autopilot on
          </span>
        )}
      </div>
      <Link
        href={AI_STACK_HREF}
        className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
        onClick={() => onRefresh?.()}
      >
        Sync & AI stack → Settings
      </Link>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  fetchPlatformSyncStatus,
  PlatformSyncStatus,
  runCopilotAutopilot,
  updateBuilderSettings,
} from '@/lib/api';
import { shortProviderName } from '@/lib/copilot-ai-stack';

const STACK_HREF = '/founder-den?tab=analytics';

type PlatformSyncStripProps = {
  accessToken: string;
  compact?: boolean;
  onMessage?: (msg: string) => void;
  onRefresh?: () => void;
};

export function PlatformSyncStrip({
  accessToken,
  compact,
  onMessage,
  onRefresh,
}: PlatformSyncStripProps) {
  const [status, setStatus] = useState<PlatformSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);

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

  async function runSync() {
    setBusy(true);
    try {
      const result = await runCopilotAutopilot('take full control and sync everything', accessToken);
      onMessage?.(result.answer);
      await load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Autopilot failed');
    } finally {
      setBusy(false);
    }
  }

  async function enableAutopilot() {
    try {
      await updateBuilderSettings(
        { autopilotEnabled: true, autopilotRedeployHosts: true, autoPublishOnEvent: true },
        accessToken,
      );
      await load();
      onMessage?.('Autopilot enabled — sync will publish and redeploy connected hosts.');
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not enable autopilot');
    }
  }

  if (!status) return null;

  const infra = status.platforms.filter((p) =>
    ['github', 'neon', 'vercel', 'railway'].includes(p.key),
  );

  return (
    <div
      className={`rounded-xl border border-zinc-800 bg-zinc-900/50 ${compact ? 'p-2' : 'p-3'}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Platform sync
          {status.autopilotEnabled ? (
            <span className="ml-2 text-emerald-400">Autopilot on</span>
          ) : null}
        </p>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            disabled={busy}
            onClick={() => void runSync()}
            className="rounded-md bg-emerald-700/80 px-2 py-1 text-[10px] font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
          >
            {busy ? 'Syncing…' : 'Sync all'}
          </button>
          {!status.autopilotEnabled ? (
            <button
              type="button"
              onClick={() => void enableAutopilot()}
              className="rounded-md border border-emerald-600/40 px-2 py-1 text-[10px] text-emerald-300 hover:bg-emerald-950/40"
            >
              Auto
            </button>
          ) : null}
          <Link href={STACK_HREF} className="rounded-md px-2 py-1 text-[10px] text-violet-400 hover:underline">
            Stack
          </Link>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {infra.map((p) => (
          <span
            key={p.key}
            title={p.detail ?? p.label}
            className={`rounded-full border px-2 py-0.5 text-[10px] ${
              p.connected
                ? 'border-emerald-600/30 bg-emerald-950/30 text-emerald-200'
                : 'border-zinc-700 text-zinc-600'
            }`}
          >
            {p.label}
            {p.connected ? ' ✓' : ''}
          </span>
        ))}
        <span
          title={status.memoryPrivacyNote}
          className="rounded-full border border-violet-600/30 bg-violet-950/20 px-2 py-0.5 text-[10px] text-violet-200"
        >
          Memory: {status.memoryStorageMode}
        </span>
        {status.chatProviders.length > 0 ? (
          <span className="rounded-full border border-sky-600/30 px-2 py-0.5 text-[10px] text-sky-200">
            Ask: {shortProviderName({ key: status.defaultProvider, label: status.defaultProvider })}
          </span>
        ) : null}
        {status.buildWorker !== 'NONE' ? (
          <span className="rounded-full border border-indigo-600/30 px-2 py-0.5 text-[10px] text-indigo-200">
            Code: {status.buildWorker}
          </span>
        ) : null}
      </div>
      {!compact && (
        <p className="mt-2 text-[10px] text-zinc-600">{status.memoryPrivacyNote}</p>
      )}
    </div>
  );
}

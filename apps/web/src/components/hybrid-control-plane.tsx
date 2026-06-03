'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { CONTROL_PLANE_MODES, type ControlPlaneModeKey } from '@dcf/utils';
import {
  fetchPlatformSyncStatus,
  PlatformSyncStatus,
  runCopilotAutopilot,
  updateBuilderSettings,
} from '@/lib/api';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

type HybridControlPlaneProps = {
  accessToken: string;
  onMessage?: (msg: string) => void;
  onRefresh?: () => void;
  autoRunWhenAutopilot?: boolean;
};

const SESSION_AUTOPILOT_KEY = 'dcf-autopilot-session-ran';

export function HybridControlPlane({
  accessToken,
  onMessage,
  onRefresh,
  autoRunWhenAutopilot = true,
}: HybridControlPlaneProps) {
  const [status, setStatus] = useState<PlatformSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);

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

  const runFullSync = useCallback(
    async (silent?: boolean) => {
      setBusy(true);
      try {
        const result = await runCopilotAutopilot(
          'take full control and push all updates to Neon, Vercel, Railway and GitHub',
          accessToken,
        );
        if (!silent) onMessage?.(result.answer);
        await load();
        onRefresh?.();
        return result;
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Control plane sync failed';
        if (!silent) onMessage?.(msg);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [accessToken, load, onMessage, onRefresh],
  );

  useEffect(() => {
    if (!autoRunWhenAutopilot || !status?.autopilotEnabled) return;
    try {
      if (sessionStorage.getItem(SESSION_AUTOPILOT_KEY)) return;
      sessionStorage.setItem(SESSION_AUTOPILOT_KEY, '1');
    } catch {
      return;
    }
    void runFullSync(true).then((r) => {
      if (r?.answer) onMessage?.(r.answer.slice(0, 500) + (r.answer.length > 500 ? '…' : ''));
    });
  }, [autoRunWhenAutopilot, status?.autopilotEnabled, runFullSync, onMessage]);

  async function setMode(mode: ControlPlaneModeKey) {
    setModeBusy(true);
    try {
      await updateBuilderSettings({ controlPlaneMode: mode }, accessToken);
      await load();
      onMessage?.(
        mode === 'FULL_STACK'
          ? 'Full stack mode — connect GitHub, Neon, Vercel, Railway, and Autopilot.'
          : 'Cursor-first mode — GitHub + optional Cursor; lighter connect path.',
      );
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not save mode');
    } finally {
      setModeBusy(false);
    }
  }

  async function enableFullStack() {
    await updateBuilderSettings(
      {
        controlPlaneMode: 'FULL_STACK',
        autopilotEnabled: true,
        autopilotRedeployHosts: true,
        autoPublishOnEvent: true,
      },
      accessToken,
    );
    await load();
    await runFullSync();
  }

  if (!status) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3 text-xs text-zinc-500">
        Loading control plane…
      </div>
    );
  }

  const cp = status.controlPlane;
  const mode = (status.controlPlaneMode ?? 'FULL_STACK') as ControlPlaneModeKey;

  return (
    <div className="space-y-3 rounded-xl border border-indigo-500/25 bg-gradient-to-br from-indigo-950/20 to-zinc-950/80 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-indigo-300">
            Hybrid control plane
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            Founder OS orchestrates · Cursor/LLMs execute · GitHub is source of truth
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void runFullSync()}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? 'Running…' : 'Take full control'}
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        {CONTROL_PLANE_MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            disabled={modeBusy}
            onClick={() => void setMode(m.key)}
            className={`rounded-lg border px-2.5 py-1.5 text-left text-[11px] transition ${
              mode === m.key
                ? 'border-indigo-400/60 bg-indigo-950/50 text-indigo-100'
                : 'border-zinc-800 text-zinc-500 hover:border-zinc-600'
            }`}
          >
            <span className="font-medium">{m.label}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {cp?.legs.map((leg) => (
          <div
            key={leg.key}
            className={`rounded-lg border p-2.5 ${
              leg.connected
                ? 'border-emerald-600/30 bg-emerald-950/20'
                : 'border-zinc-800 bg-black/30'
            }`}
          >
            <p className="text-xs font-semibold text-white">{leg.label}</p>
            <p className="text-[10px] text-zinc-500">{leg.subtitle}</p>
            <p className="mt-1 text-[10px] text-zinc-400">
              {leg.connected ? (leg.provider ?? 'Ready') : leg.detail ?? 'Not connected'}
            </p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {status.platforms
          .filter((p) => ['github', 'neon', 'vercel', 'railway'].includes(p.key))
          .map((p) => (
            <span
              key={p.key}
              className={`rounded-full border px-2 py-0.5 text-[10px] ${
                p.connected
                  ? 'border-emerald-600/40 text-emerald-200'
                  : 'border-zinc-700 text-zinc-600'
              }`}
            >
              {p.label}
              {p.connected ? ' ✓' : ''}
            </span>
          ))}
        <span className="text-[10px] text-zinc-600">
          {cp?.infraConnected ?? 0}/{cp?.infraTotal ?? 4} infra
        </span>
      </div>

      {mode === 'FULL_STACK' && (cp?.missingForFullStack.length ?? 0) > 0 && (
        <p className="text-[11px] text-amber-200/90">
          Full stack: connect {cp?.missingForFullStack.join(', ')} in{' '}
          <Link href={AI_STACK_HREF} className="underline">
            AI Stack
          </Link>
          .
        </p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-zinc-800/80 pt-3">
        {!status.autopilotEnabled ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void enableFullStack()}
            className="rounded-md border border-emerald-500/40 px-2 py-1 text-[10px] text-emerald-300 hover:bg-emerald-950/40"
          >
            Enable Autopilot + sync now
          </button>
        ) : (
          <span className="text-[10px] text-emerald-400">Autopilot on — syncs on load & deploy events</span>
        )}
        <Link href={AI_STACK_HREF} className="text-[10px] text-violet-400 hover:underline">
          Manage connections →
        </Link>
      </div>
    </div>
  );
}

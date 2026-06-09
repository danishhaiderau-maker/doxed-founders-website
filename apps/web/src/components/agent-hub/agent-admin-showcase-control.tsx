'use client';

import Link from 'next/link';
import { useState } from 'react';
import { pauseTradingAgent, resumeTradingAgent } from '@/lib/api';

export function AgentAdminShowcaseControl({
  token,
  executionPaused,
  botConnected,
  onUpdated,
}: {
  token: string;
  executionPaused?: boolean;
  botConnected?: boolean;
  onUpdated?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const stopped = executionPaused || !botConnected;

  async function toggle() {
    setBusy(true);
    setMsg(null);
    try {
      const res = stopped
        ? await resumeTradingAgent(token)
        : await pauseTradingAgent(token);
      const message =
        typeof res.message === 'string'
          ? res.message
          : typeof res.error === 'string'
            ? res.error
            : null;
      if (!res.ok) {
        setMsg(message ?? 'Command failed — check RAILWAY_TOKEN on API service');
      } else {
        setMsg(
          message ??
            (stopped
              ? 'Showcase bot starting on Railway (~60–120s)'
              : 'Showcase bot killed — Railway URL offline'),
        );
        onUpdated?.();
        if (stopped) {
          setTimeout(() => onUpdated?.(), 15000);
          setTimeout(() => onUpdated?.(), 45000);
        }
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Stop/start failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4">
      <p className="text-[10px] font-bold uppercase tracking-widest text-amber-300">Admin account</p>
      <p className="mt-1 text-xs text-zinc-400">
        <strong>Stop</strong> kills the Railway deployment —{' '}
        <a
          href="https://btc-conservative-agent-production.up.railway.app/"
          target="_blank"
          rel="noopener noreferrer"
          className="text-violet-300 hover:underline"
        >
          bot dashboard
        </a>{' '}
        goes offline (502). <strong>Start</strong> redeploys it (~2 min). Status:{' '}
        {botConnected ? 'online' : 'offline'}.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void toggle()}
          className={`rounded-lg px-4 py-2 text-sm font-bold uppercase tracking-wide disabled:opacity-50 ${
            stopped
              ? 'bg-emerald-600 text-white hover:bg-emerald-500'
              : 'bg-red-600 text-white hover:bg-red-500'
          }`}
        >
          {busy ? '…' : stopped ? '▶ Start showcase bot' : '■ Kill showcase bot'}
        </button>
        <Link
          href="/admin/control"
          className="rounded-lg border border-zinc-600 px-3 py-2 text-xs text-zinc-300 hover:text-white"
        >
          Full research dashboard →
        </Link>
      </div>
      {msg && <p className="mt-2 text-xs text-amber-200/90">{msg}</p>}
      {stopped && !busy && (
        <p className="mt-2 text-xs text-amber-300">
          Showcase offline — public agent page shows offline until you Start.
        </p>
      )}
    </div>
  );
}

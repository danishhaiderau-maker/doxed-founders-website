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

  async function toggle() {
    setBusy(true);
    setMsg(null);
    try {
      const res = executionPaused
        ? await resumeTradingAgent(token)
        : await pauseTradingAgent(token);
      if (!res.ok) {
        setMsg(typeof res.error === 'string' ? res.error : 'Command failed — check Railway bot is online');
      } else {
        setMsg(executionPaused ? 'Showcase bot resumed' : 'Showcase bot stopped');
        onUpdated?.();
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
        Pauses trading on Railway (process stays up for logs). Research dashboard will show a red STOPPED banner —{' '}
        {botConnected ? 'connected' : 'offline'}.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void toggle()}
          className={`rounded-lg px-4 py-2 text-sm font-bold uppercase tracking-wide disabled:opacity-50 ${
            executionPaused
              ? 'bg-emerald-600 text-white hover:bg-emerald-500'
              : 'bg-red-600 text-white hover:bg-red-500'
          }`}
        >
          {busy ? '…' : executionPaused ? '▶ Start showcase bot' : '■ Stop showcase bot'}
        </button>
        <Link
          href="/admin/control"
          className="rounded-lg border border-zinc-600 px-3 py-2 text-xs text-zinc-300 hover:text-white"
        >
          Full research dashboard →
        </Link>
      </div>
      {msg && <p className="mt-2 text-xs text-amber-200/90">{msg}</p>}
      {executionPaused && (
        <p className="mt-2 text-xs text-amber-300">Showcase paused — public page shows updating status.</p>
      )}
    </div>
  );
}

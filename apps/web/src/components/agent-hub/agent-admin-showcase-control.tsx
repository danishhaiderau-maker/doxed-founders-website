'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { pauseTradingAgent, resumeTradingAgent } from '@/lib/api';

const LAUNCHER = 'http://127.0.0.1:7810';

type HomeStatus = {
  bot?: { online?: boolean; dashboard?: string; lan?: string; dataDir?: string };
  analyzer?: { online?: boolean; dashboard?: string; note?: string };
  tunnel?: { url?: string | null; live?: boolean; cloudflaredRunning?: boolean };
  processes?: { botPython?: number; analyzerPython?: number };
};

type HomeCmd = {
  id: string;
  label: string;
  hint: string;
  path: string;
  tone?: 'primary' | 'danger' | 'neutral';
};

const COMMANDS: HomeCmd[] = [
  {
    id: 'start-all',
    label: '▶ Start everything',
    hint: 'Bot :7800 + analyzer + Cloudflare tunnel (3 windows)',
    path: '/cmd/start-all',
    tone: 'primary',
  },
  {
    id: 'start-bot',
    label: '▶ Start bot only',
    hint: 'btc_conservative_agent.py on :7800 with home-bot.env',
    path: '/cmd/start-bot',
    tone: 'primary',
  },
  {
    id: 'start-analyzer',
    label: '▶ Start analyzer',
    hint: 'From services/btc-conservative-agent (correct CSV folder)',
    path: '/cmd/start-analyzer',
  },
  {
    id: 'start-analyzer-once',
    label: '▶ Analyzer once',
    hint: 'Single research pass then exit',
    path: '/cmd/start-analyzer-once',
  },
  {
    id: 'start-tunnel',
    label: '▶ Start tunnel',
    hint: 'Quick trycloudflare URL → wire to Agent Hub',
    path: '/cmd/start-tunnel',
  },
  {
    id: 'wire',
    label: '☁ Wire to site',
    hint: 'Push tunnel URL to Neon + Railway (uses saved URL or paste below)',
    path: '/cmd/wire',
    tone: 'primary',
  },
  {
    id: 'stop-bot',
    label: '■ Stop bot',
    hint: 'Kill process on port 7800',
    path: '/cmd/stop-bot',
    tone: 'danger',
  },
];

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
  const [busy, setBusy] = useState<string | null>(null);
  const [execBusy, setExecBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [launcherOnline, setLauncherOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<HomeStatus | null>(null);
  const [tunnelUrl, setTunnelUrl] = useState('');

  const stopped = executionPaused || !botConnected;

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${LAUNCHER}/status`, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) {
        setLauncherOnline(false);
        setStatus(null);
        return;
      }
      const json = (await res.json()) as HomeStatus & { ok?: boolean };
      setLauncherOnline(true);
      setStatus(json);
      if (json.tunnel?.url && !tunnelUrl) setTunnelUrl(json.tunnel.url);
    } catch {
      setLauncherOnline(false);
      setStatus(null);
    }
  }, [tunnelUrl]);

  useEffect(() => {
    void refreshStatus();
    const t = setInterval(() => void refreshStatus(), 15000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  async function runLocal(path: string, id: string) {
    setBusy(id);
    setMsg(null);
    try {
      const q = id === 'wire' && tunnelUrl.trim() ? `?url=${encodeURIComponent(tunnelUrl.trim())}` : '';
      const res = await fetch(`${LAUNCHER}${path}${q}`, { signal: AbortSignal.timeout(120000) });
      const json = (await res.json()) as { ok?: boolean; message?: string; error?: string; log?: string };
      if (!json.ok) {
        setMsg(json.error ?? 'Command failed');
      } else {
        setMsg(json.message ?? 'Done');
        if (json.log) setMsg((m) => `${m ?? ''}\n${json.log}`.trim());
        void refreshStatus();
        onUpdated?.();
        if (id.startsWith('start') || id === 'wire') {
          setTimeout(() => onUpdated?.(), 20000);
          setTimeout(() => onUpdated?.(), 60000);
        }
      }
    } catch {
      setMsg('Local bridge offline — run START-LAUNCHER.cmd on this PC first.');
      setLauncherOnline(false);
    } finally {
      setBusy(null);
    }
  }

  async function toggleExecution() {
    setExecBusy(true);
    setMsg(null);
    try {
      const res = stopped ? await resumeTradingAgent(token) : await pauseTradingAgent(token);
      const message =
        typeof res.message === 'string'
          ? res.message
          : typeof res.error === 'string'
            ? res.error
            : null;
      setMsg(message ?? (res.ok ? 'Execution updated' : 'Failed — is home bot online?'));
      if (res.ok) {
        onUpdated?.();
        setTimeout(() => onUpdated?.(), 15000);
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Pause/resume failed');
    } finally {
      setExecBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 p-4">
      <p className="text-[10px] font-bold uppercase tracking-widest text-amber-300">
        Home PC command center
      </p>
      <p className="mt-1 text-xs text-zinc-400">
        Controls your <strong>local</strong> showcase bot (not old Railway). Works when you open{' '}
        <a href="https://doxxedcrypto.digital/agent-hub/conservative-btc" className="text-violet-300 hover:underline">
          Agent Hub
        </a>{' '}
        on the <strong>same PC</strong> as the bot. Site mirror:{' '}
        {botConnected ? (
          <span className="text-emerald-400">online</span>
        ) : (
          <span className="text-red-400">offline</span>
        )}
        .
      </p>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {status && (
          <>
            <StatusChip label="Bot :7800" ok={Boolean(status.bot?.online)} />
            <StatusChip label="Analyzer :9001" ok={Boolean(status.analyzer?.online)} />
            <StatusChip label="Tunnel" ok={Boolean(status.tunnel?.live)} />
            <StatusChip label="Bridge :7810" ok={launcherOnline === true} />
          </>
        )}
      </div>

      <p className="mt-3 text-[10px] font-bold uppercase tracking-widest text-zinc-500">
        Local commands (this PC)
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {COMMANDS.map((cmd) => (
          <button
            key={cmd.id}
            type="button"
            disabled={busy !== null}
            title={cmd.hint}
            onClick={() => void runLocal(cmd.path, cmd.id)}
            className={`rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-50 ${
              cmd.tone === 'primary'
                ? 'bg-violet-600 text-white hover:bg-violet-500'
                : cmd.tone === 'danger'
                  ? 'bg-red-700 text-white hover:bg-red-600'
                  : 'border border-zinc-600 text-zinc-200 hover:border-violet-500/50'
            }`}
          >
            {busy === cmd.id ? '…' : cmd.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[10px] text-zinc-500">
          Tunnel URL (for Wire to site)
          <input
            value={tunnelUrl}
            onChange={(e) => setTunnelUrl(e.target.value)}
            placeholder="https://….trycloudflare.com"
            className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200"
          />
        </label>
        <button
          type="button"
          disabled={execBusy}
          onClick={() => void toggleExecution()}
          className={`rounded-lg px-4 py-2 text-sm font-bold disabled:opacity-50 ${
            stopped
              ? 'bg-emerald-600 text-white hover:bg-emerald-500'
              : 'bg-amber-700 text-white hover:bg-amber-600'
          }`}
        >
          {execBusy ? '…' : stopped ? '▶ Resume trading' : '■ Pause trading'}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <a
          href="http://127.0.0.1:7800"
          target="_blank"
          rel="noreferrer"
          className="text-violet-300 hover:underline"
        >
          Bot dashboard →
        </a>
        <a
          href="http://127.0.0.1:9001"
          target="_blank"
          rel="noreferrer"
          className="text-violet-300 hover:underline"
        >
          Analyzer :9001 →
        </a>
        <button type="button" onClick={() => void refreshStatus()} className="text-zinc-500 hover:text-white">
          Refresh status
        </button>
        <Link href="/admin/control" className="text-zinc-500 hover:text-white">
          Admin control →
        </Link>
      </div>

      <p className="mt-2 text-[10px] text-zinc-600">
        Step 0 (once per session): double-click{' '}
        <code className="text-zinc-400">START-LAUNCHER.cmd</code> in the repo folder. Then use buttons
        above — no CMD copy-paste.
      </p>

      {status?.analyzer?.note && (
        <p className="mt-2 text-[10px] text-amber-400/90">{status.analyzer.note}</p>
      )}

      {msg && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-amber-200/90">{msg}</pre>}
    </div>
  );
}

function StatusChip({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div
      className={`rounded-lg border px-2 py-1.5 text-[11px] ${
        ok ? 'border-emerald-500/40 text-emerald-300' : 'border-zinc-700 text-zinc-500'
      }`}
    >
      {label}: {ok ? 'online' : 'offline'}
    </div>
  );
}

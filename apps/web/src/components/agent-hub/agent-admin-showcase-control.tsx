'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { pauseTradingAgent, resumeTradingAgent } from '@/lib/api';

const LAUNCHER = 'http://127.0.0.1:7810';
const DEFAULT_BOT_PORT = 7002;
const DEFAULT_ANALYZER_PORT = 9500;
const PUBLIC_BOT_URL = 'https://bot.doxxedcrypto.digital';

type HomeStatus = {
  mode?: string;
  stackLabel?: string;
  ports?: { bot?: number; analyzer?: number; launcher?: number };
  bot?: { online?: boolean; ok?: boolean; dashboard?: string; lan?: string; dataDir?: string };
  analyzer?: { online?: boolean; ok?: boolean; dashboard?: string; note?: string };
  tunnel?: { url?: string | null; live?: boolean; cloudflaredRunning?: boolean; enabled?: boolean };
};

function botPortFrom(status: HomeStatus | null): number {
  return status?.ports?.bot ?? DEFAULT_BOT_PORT;
}

function analyzerPortFrom(status: HomeStatus | null): number {
  return status?.ports?.analyzer ?? DEFAULT_ANALYZER_PORT;
}

function isOnline(section?: { online?: boolean; ok?: boolean } | null): boolean {
  if (!section) return false;
  if (typeof section.online === 'boolean') return section.online;
  return Boolean(section.ok);
}

async function probeLocalHealth(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(2500) });
    return res.ok;
  } catch {
    return false;
  }
}

async function probeDirectHomeStatus(): Promise<HomeStatus> {
  const botPort = DEFAULT_BOT_PORT;
  const analyzerPort = DEFAULT_ANALYZER_PORT;
  const [botOk, analyzerOk, tunnelOk] = await Promise.all([
    probeLocalHealth(`http://127.0.0.1:${botPort}/api/ping`),
    probeLocalHealth(`http://127.0.0.1:${analyzerPort}/api/status`),
    probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`),
  ]);
  return {
    mode: 'production',
    ports: { bot: botPort, analyzer: analyzerPort, launcher: 7810 },
    bot: { online: botOk, dashboard: `http://127.0.0.1:${botPort}` },
    analyzer: { online: analyzerOk, dashboard: `http://127.0.0.1:${analyzerPort}/` },
    tunnel: { live: tunnelOk, url: PUBLIC_BOT_URL, enabled: true, cloudflaredRunning: tunnelOk },
  };
}

async function normalizeHomeStatus(raw: HomeStatus & { ok?: boolean }): Promise<HomeStatus> {
  const botPort = raw.ports?.bot ?? DEFAULT_BOT_PORT;
  const analyzerPort = raw.ports?.analyzer ?? DEFAULT_ANALYZER_PORT;
  const botDash = raw.bot?.dashboard ?? `http://127.0.0.1:${botPort}`;
  const analyzerDash = raw.analyzer?.dashboard ?? `http://127.0.0.1:${analyzerPort}/`;

  if (raw.ok) {
    const [tunnelProbe] = await Promise.all([
      raw.tunnel?.live === undefined || raw.tunnel?.live === false
        ? probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`)
        : Promise.resolve(Boolean(raw.tunnel?.live)),
    ]);
    return {
      ...raw,
      bot: { ...raw.bot, online: Boolean(raw.bot?.online), dashboard: botDash },
      analyzer: {
        ...raw.analyzer,
        online: Boolean(raw.analyzer?.online),
        dashboard: analyzerDash,
      },
      tunnel: {
        ...raw.tunnel,
        live: Boolean(raw.tunnel?.live) || tunnelProbe,
        url: raw.tunnel?.url ?? PUBLIC_BOT_URL,
        enabled: raw.tunnel?.enabled ?? true,
      },
    };
  }

  const botOnline = isOnline(raw.bot);
  const analyzerOnline = isOnline(raw.analyzer);
  const tunnelLive = Boolean(raw.tunnel?.live);

  const needsBotProbe = !botOnline && raw.bot?.online === undefined && raw.bot?.ok === undefined;
  const needsAnalyzerProbe =
    !analyzerOnline && raw.analyzer?.online === undefined && raw.analyzer?.ok === undefined;
  const needsTunnelProbe = !tunnelLive && raw.tunnel?.live === undefined;

  const [botProbe, analyzerProbe, tunnelProbe] = await Promise.all([
    needsBotProbe ? probeLocalHealth(`${botDash}/api/ping`) : Promise.resolve(botOnline),
    needsAnalyzerProbe ? probeLocalHealth(`${analyzerDash}api/status`) : Promise.resolve(analyzerOnline),
    needsTunnelProbe ? probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`) : Promise.resolve(tunnelLive),
  ]);

  return {
    ...raw,
    bot: { ...raw.bot, online: botOnline || botProbe, dashboard: botDash },
    analyzer: {
      ...raw.analyzer,
      online: analyzerOnline || analyzerProbe,
      dashboard: analyzerDash,
    },
    tunnel: {
      ...raw.tunnel,
      live: tunnelLive || tunnelProbe,
      url: raw.tunnel?.url ?? PUBLIC_BOT_URL,
      enabled: raw.tunnel?.enabled ?? true,
    },
  };
}

type HomeCmd = {
  id: string;
  label: string;
  hint: string;
  path: string;
  tone?: 'primary' | 'danger' | 'neutral';
};

const INSTANT_CMD_TIMEOUT_MS = 15000;
const SLOW_CMD_TIMEOUT_MS = 120000;

function cmdTimeoutMs(id: string): number {
  if (id === 'wipe-research') return SLOW_CMD_TIMEOUT_MS;
  if (id === 'start-tunnel') return 60_000;
  return INSTANT_CMD_TIMEOUT_MS;
}

const COMMANDS: HomeCmd[] = [
  {
    id: 'reset-home-stack',
    label: '↻ Reset home stack',
    hint: 'Clean stop → wait 8s → start fresh (best when :7002/tunnel flaps or duplicate bots)',
    path: '/cmd/reset-home-stack',
    tone: 'primary',
  },
  {
    id: 'start-all',
    label: '▶ Start everything',
    hint: 'Reload bridge + open bot :7002, analyzer :9500, tunnel (all visible consoles stay open)',
    path: '/cmd/start-all-global',
    tone: 'primary',
  },
  {
    id: 'restart-bridge',
    label: '↻ Restart bridge',
    hint: 'Reload RESTART-LAUNCHER logic — opens Doxed Home Bridge :7810 (required for buttons)',
    path: '/cmd/restart-bridge',
    tone: 'primary',
  },
  {
    id: 'start-bot',
    label: '▶ Start bot',
    hint: 'Conservative BTC agent on :7002 (signals + relay webhook for site subscribers)',
    path: '/cmd/start-bot',
    tone: 'primary',
  },
  {
    id: 'start-analyzer',
    label: '▶ Start analyzer',
    hint: 'Research loop + dashboard on :9500',
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
    hint: 'Named tunnel bot.doxxedcrypto.digital → :7002. Requires bot running first.',
    path: '/cmd/start-tunnel',
  },
  {
    id: 'wire',
    label: '☁ Wire to site',
    hint: 'Push tunnel URL to Neon + Railway so doxxedcrypto.digital can reach home bot',
    path: '/cmd/wire',
    tone: 'primary',
  },
  {
    id: 'wipe-research',
    label: '🗑 Wipe research CSVs',
    hint: 'Fresh collection reset — archive + wipe CSV/JSONL, restart at $500',
    path: '/cmd/wipe-research',
    tone: 'danger',
  },
  {
    id: 'stop-bot',
    label: '■ Stop bot',
    hint: 'Stop showcase bot on :7002',
    path: '/cmd/stop-bot',
    tone: 'danger',
  },
  {
    id: 'stop-all-global',
    label: '■ Stop everything',
    hint: 'Stop :7002 bot, :9500 analyzer, and tunnel',
    path: '/cmd/stop-all-global',
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
  const [tunnelUrl, setTunnelUrl] = useState(PUBLIC_BOT_URL);

  const stopped = executionPaused || !botConnected;
  const botPort = botPortFrom(status);
  const analyzerPort = analyzerPortFrom(status);
  const botDash = status?.bot?.dashboard ?? `http://127.0.0.1:${botPort}`;
  const analyzerDash = status?.analyzer?.dashboard ?? `http://127.0.0.1:${analyzerPort}/`;

  const refreshStatus = useCallback(async () => {
    let bridgeOk = false;
    try {
      const healthRes = await fetch(`${LAUNCHER}/health`, { signal: AbortSignal.timeout(8000) });
      bridgeOk = healthRes.ok;
    } catch {
      bridgeOk = false;
    }
    setLauncherOnline(bridgeOk);

    try {
      const res = await fetch(`${LAUNCHER}/status`, { signal: AbortSignal.timeout(15000) });
      if (!res.ok) {
        setStatus(await probeDirectHomeStatus());
        return;
      }
      const json = (await res.json()) as HomeStatus & { ok?: boolean };
      const normalized = await normalizeHomeStatus(json);
      setStatus(normalized);
      if (normalized.tunnel?.url) setTunnelUrl(normalized.tunnel.url);
    } catch {
      setStatus(await probeDirectHomeStatus());
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const t = setInterval(() => void refreshStatus(), 60_000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  async function runLocal(path: string, id: string) {
    setBusy(id);
    setMsg(null);
    try {
      const q = id === 'wire' && tunnelUrl.trim() ? `?url=${encodeURIComponent(tunnelUrl.trim())}` : '';
      const res = await fetch(`${LAUNCHER}${path}${q}`, { signal: AbortSignal.timeout(cmdTimeoutMs(id)) });
      const json = (await res.json()) as { ok?: boolean; message?: string; error?: string; log?: string };
      if (!json.ok) {
        setMsg(json.error ?? 'Command failed — run RESTART-LAUNCHER.cmd on this PC');
      } else {
        setMsg(json.message ?? 'Done');
        if (json.log) setMsg((m) => `${m ?? ''}\n${json.log}`.trim());
        void refreshStatus();
        onUpdated?.();
        if (id === 'start-all' || id === 'restart-bridge' || id === 'stop-all-global' || id === 'reset-home-stack') {
          setTimeout(() => void refreshStatus(), 5000);
          setTimeout(() => void refreshStatus(), 15000);
          setTimeout(() => void refreshStatus(), 45000);
          setTimeout(() => void refreshStatus(), 90000);
        }
        if (id.startsWith('start') || id === 'wire') {
          setTimeout(() => onUpdated?.(), 20000);
          setTimeout(() => onUpdated?.(), 60000);
        }
      }
    } catch (err) {
      let bridgeAlive = false;
      try {
        const healthRes = await fetch(`${LAUNCHER}/health`, { signal: AbortSignal.timeout(2500) });
        bridgeAlive = healthRes.ok;
      } catch {
        bridgeAlive = false;
      }
      const hint = bridgeAlive
        ? 'Bridge is up but the command timed out — check Doxed console windows, then click Refresh status.'
        : err instanceof Error && /fetch|network|Failed/i.test(err.message)
          ? 'Browser blocked localhost — run RESTART-LAUNCHER.cmd on this PC, then hard-refresh this page.'
          : 'Bridge offline — double-click RESTART-LAUNCHER.cmd or RECOVER-GLOBAL-STACK.cmd in the repo folder.';
      setMsg(hint);
      setLauncherOnline(bridgeAlive);
    } finally {
      setBusy(null);
    }
  }

  async function toggleExecution() {
    setExecBusy(true);
    setMsg(null);
    try {
      if (status?.bot?.online) {
        const path = stopped ? '/cmd/resume-trading' : '/cmd/pause-trading';
        const localRes = await fetch(`${LAUNCHER}${path}`, { signal: AbortSignal.timeout(30000) });
        const localJson = (await localRes.json()) as { ok?: boolean; message?: string; error?: string };
        if (localJson.ok) {
          setMsg(localJson.message ?? (stopped ? 'Bot resumed locally.' : 'Bot paused locally.'));
          onUpdated?.();
          setTimeout(() => onUpdated?.(), 5000);
          return;
        }
        if (localJson.error) {
          setMsg(localJson.error);
          return;
        }
      }

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
        Runs the <strong>doxxedcrypto.digital</strong> showcase stack on this PC: conservative BTC signals
        at <strong>:{botPort}</strong>, research at <strong>:{analyzerPort}</strong>, bridge at{' '}
        <strong>:7810</strong>. The public site and Bitfinex relay reach your bot through the Cloudflare
        tunnel. Open{' '}
        <a href="https://doxxedcrypto.digital/agent-hub/conservative-btc" className="text-violet-300 hover:underline">
          Agent Hub
        </a>{' '}
        on this same machine to use these controls. Site mirror:{' '}
        {botConnected ? (
          <span className="text-emerald-400">online</span>
        ) : (
          <span className="text-red-400">offline</span>
        )}
        .
      </p>

      {launcherOnline === false && (
        <p className="mt-2 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs text-red-200">
          Bridge :7810 is offline — click <strong>Restart bridge</strong> below, or double-click{' '}
          <code className="text-red-100">RESTART-LAUNCHER.cmd</code>, then hard-refresh this page.
        </p>
      )}

      <div className="mt-3 rounded-lg border border-zinc-700/80 bg-zinc-950/50 px-3 py-2.5 text-xs text-zinc-300">
        <p className="font-semibold text-zinc-200">How to use (this PC)</p>
        <ol className="mt-1.5 list-decimal space-y-1 pl-4 text-zinc-400">
          <li>
            <strong className="text-zinc-300">Reset home stack</strong> when bot/tunnel keeps flapping, or after code
            updates — clean stop, 8s pause, fresh start.
          </li>
          <li>
            Or <strong className="text-zinc-300">Start everything</strong> if stack is already stopped (bridge + bot +
            analyzer + tunnel).
          </li>
          <li>
            <strong className="text-zinc-300">/api/ping</strong> on :{botPort} responds in ~1–2s while bot loads; full
            dashboard takes <strong className="text-zinc-300">60–90s</strong> on this PC.
          </li>
          <li>
            Click <strong className="text-zinc-300">Refresh status</strong> at 30s, 60s, 90s — bot, analyzer, tunnel
            should go green.
          </li>
          <li>
            Optional: <strong className="text-zinc-300">Wire to site</strong> after tunnel is live (Neon + Railway).
          </li>
        </ol>
        <p className="mt-2 text-[10px] text-zinc-500">
          Do not press Enter in bot/analyzer/tunnel windows unless stopping them. Bridge :7810 must stay open. Close
          extra browser tabs if the PC feels slow (duplicate bots are auto-killed on start).
        </p>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <StatusChip label="Bridge :7810" ok={launcherOnline === true} />
        <StatusChip label={`Showcase bot :${botPort}`} ok={Boolean(status?.bot?.online)} />
        <StatusChip label={`Analyzer :${analyzerPort}`} ok={Boolean(status?.analyzer?.online)} />
        <StatusChip label="Cloudflare tunnel" ok={Boolean(status?.tunnel?.live)} sub={PUBLIC_BOT_URL} />
        <StatusChip label="Site mirror (Railway)" ok={Boolean(botConnected)} />
      </div>

      <p className="mt-3 text-[10px] font-bold uppercase tracking-widest text-zinc-500">
        Showcase controls (this PC)
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {COMMANDS.map((cmd) => (
          <button
            key={cmd.id}
            type="button"
            disabled={busy === cmd.id}
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
            placeholder={PUBLIC_BOT_URL}
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
        <a href={botDash} target="_blank" rel="noreferrer" className="text-violet-300 hover:underline">
          Bot dashboard :{botPort} →
        </a>
        <a href={analyzerDash} target="_blank" rel="noreferrer" className="text-violet-300 hover:underline">
          Analyzer :{analyzerPort} →
        </a>
        <a href={PUBLIC_BOT_URL} target="_blank" rel="noreferrer" className="text-violet-300 hover:underline">
          Public bot URL →
        </a>
        <a
          href={`${botDash}/api/export_csv`}
          target="_blank"
          rel="noreferrer"
          className="text-violet-300 hover:underline"
        >
          Download CSV/JSONL (zip) →
        </a>
        <button type="button" onClick={() => void refreshStatus()} className="text-zinc-500 hover:text-white">
          Refresh status
        </button>
        <Link href="/admin/control" className="text-zinc-500 hover:text-white">
          Admin control →
        </Link>
      </div>

      <p className="mt-2 text-[10px] text-zinc-600">
        Named Cloudflare tunnel (free plan) → bot.doxxedcrypto.digital. Use <strong>Start everything</strong> for the
        full stack; use <strong>Restart bridge</strong> if buttons stop responding.
      </p>

      {status?.analyzer?.note && (
        <p className="mt-2 text-[10px] text-amber-400/90">{status.analyzer.note}</p>
      )}

      {msg && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-amber-200/90">{msg}</pre>}
    </div>
  );
}

function StatusChip({
  label,
  ok,
  hidden,
  sub,
}: {
  label: string;
  ok: boolean;
  hidden?: boolean;
  sub?: string;
}) {
  if (hidden) return null;
  return (
    <div
      className={`flex flex-col gap-0.5 rounded-lg border px-2 py-1.5 text-[11px] ${
        ok ? 'border-emerald-500/40 text-emerald-300' : 'border-zinc-700 text-zinc-500'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${
            ok ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]' : 'bg-zinc-600'
          }`}
          title={ok ? 'online' : 'offline'}
          aria-hidden
        />
        <span>
          {label}: <strong>{ok ? 'online' : 'offline'}</strong>
        </span>
      </div>
      {sub && <span className="truncate pl-4 text-[9px] text-zinc-600">{sub}</span>}
    </div>
  );
}

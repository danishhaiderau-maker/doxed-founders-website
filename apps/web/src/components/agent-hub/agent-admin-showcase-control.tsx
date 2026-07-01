'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { flyControlBot, getShowcaseHost, pauseTradingAgent, resumeTradingAgent, fetchServerBotHealth } from '@/lib/api';

const LAUNCHER = 'http://127.0.0.1:7810';
const DEFAULT_BOT_PORT = 7002;
const DEFAULT_ANALYZER_PORT = 9001; // :9500 was a phantom port — bot.py confirms nothing listens there
const PUBLIC_BOT_URL = 'https://bot.doxxedcrypto.digital';
const FLY_BOT_URL = 'https://doxed-btc-bot.fly.dev';

type HomeStatus = {
  mode?: string;
  stackLabel?: string;
  ports?: { bot?: number; analyzer?: number; launcher?: number };
  bot?: { online?: boolean; ok?: boolean; dashboard?: string; lan?: string; dataDir?: string };
  analyzer?: { online?: boolean; ok?: boolean; dashboard?: string; note?: string };
  tunnel?: { url?: string | null; live?: boolean; cloudflaredRunning?: boolean; enabled?: boolean };
  fly?: { online?: boolean; url?: string };
};

function botPortFrom(status: HomeStatus | null): number {
  return status?.ports?.bot ?? DEFAULT_BOT_PORT;
}

function analyzerPortFrom(): number {
  // The global showcase analyzer always listens on :9001 (bot.py confirms nothing runs
  // on :9500 — that was a phantom port). Hardcoded so the command-center label never
  // shows :9500 again, regardless of any stale port reported by the home status payload.
  return DEFAULT_ANALYZER_PORT;
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
  const [botOk, analyzerOk, tunnelOk, flyOk] = await Promise.all([
    probeLocalHealth(`http://127.0.0.1:${botPort}/api/ping`),
    probeLocalHealth(`http://127.0.0.1:${analyzerPort}/api/status`),
    probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`),
    probeLocalHealth(`${FLY_BOT_URL}/api/ping`),
  ]);
  return {
    mode: 'production',
    ports: { bot: botPort, analyzer: analyzerPort, launcher: 7810 },
    bot: { online: botOk, dashboard: `http://127.0.0.1:${botPort}` },
    analyzer: { online: analyzerOk, dashboard: `http://127.0.0.1:${analyzerPort}/` },
    tunnel: { live: tunnelOk, url: PUBLIC_BOT_URL, enabled: true, cloudflaredRunning: tunnelOk },
    fly: { online: flyOk, url: FLY_BOT_URL },
  };
}

async function normalizeHomeStatus(raw: HomeStatus & { ok?: boolean }): Promise<HomeStatus> {
  const botPort = raw.ports?.bot ?? DEFAULT_BOT_PORT;
  // Analyzer is always :9001 in the global showcase — ignore stale raw.ports.analyzer.
  const analyzerPort = DEFAULT_ANALYZER_PORT;
  const botDash = raw.bot?.dashboard ?? `http://127.0.0.1:${botPort}`;
  const analyzerDash = raw.analyzer?.dashboard ?? `http://127.0.0.1:${analyzerPort}/`;

  if (raw.ok) {
    const [tunnelProbe, flyProbe] = await Promise.all([
      raw.tunnel?.live === undefined || raw.tunnel?.live === false
        ? probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`)
        : Promise.resolve(Boolean(raw.tunnel?.live)),
      raw.fly?.online === undefined
        ? probeLocalHealth(`${FLY_BOT_URL}/api/ping`)
        : Promise.resolve(Boolean(raw.fly?.online)),
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
      fly: { online: Boolean(raw.fly?.online) || flyProbe, url: FLY_BOT_URL },
    };
  }

  const botOnline = isOnline(raw.bot);
  const analyzerOnline = isOnline(raw.analyzer);
  const tunnelLive = Boolean(raw.tunnel?.live);
  const flyOnline = Boolean(raw.fly?.online);

  const needsBotProbe = !botOnline && raw.bot?.online === undefined && raw.bot?.ok === undefined;
  const needsAnalyzerProbe =
    !analyzerOnline && raw.analyzer?.online === undefined && raw.analyzer?.ok === undefined;
  const needsTunnelProbe = !tunnelLive && raw.tunnel?.live === undefined;
  const needsFlyProbe = !flyOnline && raw.fly?.online === undefined;

  const [botProbe, analyzerProbe, tunnelProbe, flyProbe] = await Promise.all([
    needsBotProbe ? probeLocalHealth(`${botDash}/api/ping`) : Promise.resolve(botOnline),
    needsAnalyzerProbe ? probeLocalHealth(`${analyzerDash}api/status`) : Promise.resolve(analyzerOnline),
    needsTunnelProbe ? probeLocalHealth(`${PUBLIC_BOT_URL}/api/ping`) : Promise.resolve(tunnelLive),
    needsFlyProbe ? probeLocalHealth(`${FLY_BOT_URL}/api/ping`) : Promise.resolve(flyOnline),
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
    fly: { online: flyOnline || flyProbe, url: FLY_BOT_URL },
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

/** One-click orchestration — same sequence as home-stack-start-everything.ps1 */
const START_SHOWCASE: HomeCmd = {
  id: 'start-showcase',
  label: '▶ Start showcase',
  hint: 'Bridge :7810 → bot :7002 → analyzer :9001 → tunnel → auto-wire (correct order, one click)',
  path: '/cmd/start-all-global',
  tone: 'primary',
};

const STOP_SHOWCASE: HomeCmd = {
  id: 'stop-showcase',
  label: '■ Stop showcase',
  hint: 'Stop bot, analyzer, and tunnel (bridge :7810 stays running for next Start)',
  path: '/cmd/stop-all-global',
  tone: 'danger',
};

const ADVANCED_COMMANDS: HomeCmd[] = [
  {
    id: 'reset-home-stack',
    label: '↻ Clean reset + start',
    hint: 'Stop everything, wait 8s, then run the full Start sequence (use when stack keeps flapping)',
    path: '/cmd/reset-home-stack',
    tone: 'primary',
  },
  {
    id: 'restart-bridge',
    label: '↻ Restart bridge only',
    hint: 'Reload bridge :7810 if Start/Stop buttons stop responding',
    path: '/cmd/restart-bridge',
  },
  {
    id: 'wipe-research',
    label: '🗑 Wipe research CSVs',
    hint: 'Fresh collection reset — archive + wipe CSV/JSONL, restart at $500',
    path: '/cmd/wipe-research',
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
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [startSteps, setStartSteps] = useState<string | null>(null);
  const [showcaseHost, setShowcaseHost] = useState<'fly' | 'local'>('local');
  const [serverFlyOnline, setServerFlyOnline] = useState<boolean | null>(null);
  const isFly = showcaseHost === 'fly';

  const stopped = executionPaused || !botConnected;
  const botPort = botPortFrom(status);
  const analyzerPort = analyzerPortFrom();
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
    } catch {
      setStatus(await probeDirectHomeStatus());
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const t = setInterval(() => void refreshStatus(), 60_000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  // Server-side Fly.io + Cloudflare reachability probe. The browser-side probe of
  // Fly fails on CORS/region (false negative), so the command center relies on this
  // server-side signal as the primary source for the "Fly bot (sin)" status chip.
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const json = await fetchServerBotHealth('conservative-btc');
        if (!cancelled) setServerFlyOnline(Boolean(json.fly || json.cloudflare || json.ok));
      } catch {
        // leave as-is; client-side probe remains a secondary fallback
      }
    };
    void probe();
    const t = setInterval(() => void probe(), 60_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const json = await getShowcaseHost();
        if (!cancelled && json.host) setShowcaseHost(json.host);
      } catch {
        // default to local
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function runLocal(path: string, id: string) {
    setBusy(id);
    setMsg(null);
    try {
      const res = await fetch(`${LAUNCHER}${path}`, { signal: AbortSignal.timeout(cmdTimeoutMs(id)) });
      const json = (await res.json()) as { ok?: boolean; message?: string; error?: string; log?: string };
      if (!json.ok) {
        setMsg(json.error ?? 'Command failed — run RESTART-LAUNCHER.cmd on this PC');
      } else {
        setMsg(json.message ?? 'Done');
        if (json.log) setMsg((m) => `${m ?? ''}\n${json.log}`.trim());
        void refreshStatus();
        onUpdated?.();
        if (
          id === 'start-showcase' ||
          id === 'restart-bridge' ||
          id === 'stop-showcase' ||
          id === 'reset-home-stack'
        ) {
          setTimeout(() => void refreshStatus(), 5000);
          setTimeout(() => void refreshStatus(), 15000);
          setTimeout(() => void refreshStatus(), 45000);
          setTimeout(() => void refreshStatus(), 90000);
        }
        if (id === 'start-showcase' || id === 'reset-home-stack') {
          setStartSteps(
            'Starting in order: (1) bridge :7810 → (2) bot :7002 → (3) analyzer :9001 → (4) tunnel → (5) auto-wire. Four console windows should open — keep them open. Refresh this page at 30s, 60s, 90s.',
          );
          setTimeout(() => onUpdated?.(), 20000);
          setTimeout(() => onUpdated?.(), 60000);
          setTimeout(() => onUpdated?.(), 120000);
        }
        if (id === 'stop-showcase') {
          setStartSteps(null);
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

  async function runFly(action: 'start' | 'stop', id: string) {
    setBusy(id);
    setMsg(null);
    setStartSteps(
      action === 'start'
        ? 'Starting remote Fly bot — scaling the Fly machine up. Polling until /api/ping is healthy (up to ~90s)...'
        : 'Stopping remote Fly bot — gracefully stopping the Fly machine (preserved, not destroyed)...',
    );
    try {
      const res = await flyControlBot(token, action);
      setMsg(res.message ?? (res.ok ? `Fly bot ${action}ed.` : `Fly ${action} did not confirm.`));
      if (res.ok) {
        onUpdated?.();
        setTimeout(() => onUpdated?.(), 5000);
        setTimeout(() => onUpdated?.(), 15000);
      }
      if (action === 'stop') setStartSteps(null);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : `Fly ${action} failed`);
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
        {isFly && (
          <span className="ml-2 rounded bg-sky-500/20 px-1.5 py-0.5 text-sky-300">Fly remote mode</span>
        )}
      </p>
      <p className="mt-1 text-xs text-zinc-400">
        Runs the <strong>doxxedcrypto.digital</strong> showcase stack on this PC: conservative BTC signals
        at <strong>:{botPort}</strong>, research at <strong>:{analyzerPort}</strong>, bridge at{' '}
        <strong>:7810</strong>. The public site and Bitfinex relay reach your bot through{' '}
        <strong className="text-sky-300">Fly.io</strong> (primary, stable) with the Cloudflare tunnel as a
        fallback. Open{' '}
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
          Bridge :7810 is offline — click <strong>Start showcase</strong> below (step 1 reloads the bridge), or
          double-click <code className="text-red-100">RESTART-LAUNCHER.cmd</code>, then hard-refresh this page.
        </p>
      )}

      <div className="mt-3 rounded-lg border border-zinc-700/80 bg-zinc-950/50 px-3 py-2.5 text-xs text-zinc-300">
        <p className="font-semibold text-zinc-200">One-click on this PC</p>
        <p className="mt-1.5 text-zinc-400">
          <strong className="text-zinc-300">Start showcase</strong> runs the full stack in the correct order (bridge →
          bot → analyzer → tunnel → wire). <strong className="text-zinc-300">Stop showcase</strong> shuts down bot,
          analyzer, and tunnel. After Start, wait <strong className="text-zinc-300">60–90s</strong> for the live Agent
          Hub page to sync — &quot;Live bot slow&quot; clears once the tunnel and bot are up.
        </p>
        <p className="mt-2 text-[10px] text-zinc-500">
          Keep the four Doxed console windows open. Do not press Enter in them unless stopping. Use Advanced only for
          clean reset or bridge-only restart.
        </p>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <StatusChip label="Bridge :7810" ok={launcherOnline === true} />
        <StatusChip label={`Showcase bot :${botPort}`} ok={Boolean(status?.bot?.online)} />
        <StatusChip label={`Analyzer :${analyzerPort}`} ok={Boolean(status?.analyzer?.online)} />
        <StatusChip
          label="Fly bot (sin)"
          ok={Boolean(serverFlyOnline ?? botConnected ?? status?.fly?.online)}
          sub={FLY_BOT_URL}
        />
        <StatusChip label="Cloudflare tunnel" ok={Boolean(status?.tunnel?.live)} sub={PUBLIC_BOT_URL} />
        <StatusChip label="Site mirror (Railway)" ok={Boolean(botConnected)} />
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy === START_SHOWCASE.id}
          title={isFly ? 'Start the remote Fly.io bot machine (scale up)' : START_SHOWCASE.hint}
          onClick={() => void (isFly ? runFly('start', START_SHOWCASE.id) : runLocal(START_SHOWCASE.path, START_SHOWCASE.id))}
          className="min-w-[160px] flex-1 rounded-xl bg-emerald-600 px-5 py-3 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-50 sm:flex-none"
        >
          {busy === START_SHOWCASE.id ? 'Starting…' : START_SHOWCASE.label}
        </button>
        <button
          type="button"
          disabled={busy === STOP_SHOWCASE.id}
          title={isFly ? 'Stop the remote Fly.io bot machine (graceful, preserved)' : STOP_SHOWCASE.hint}
          onClick={() => void (isFly ? runFly('stop', STOP_SHOWCASE.id) : runLocal(STOP_SHOWCASE.path, STOP_SHOWCASE.id))}
          className="min-w-[160px] flex-1 rounded-xl bg-red-700 px-5 py-3 text-sm font-bold text-white hover:bg-red-600 disabled:opacity-50 sm:flex-none"
        >
          {busy === STOP_SHOWCASE.id ? 'Stopping…' : STOP_SHOWCASE.label}
        </button>
      </div>

      {startSteps && (
        <p className="mt-2 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
          {startSteps}
        </p>
      )}

      <div className="mt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((o) => !o)}
          className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 hover:text-zinc-300"
        >
          {advancedOpen ? '▾ Advanced' : '▸ Advanced'}
        </button>
        {advancedOpen && (
          <div className="mt-2 flex flex-wrap gap-2">
            {ADVANCED_COMMANDS.map((cmd) => (
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
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
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
        <a href={FLY_BOT_URL} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">
          Fly bot (sin) →
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
        Named Cloudflare tunnel → bot.doxxedcrypto.digital. Start showcase handles bridge, bot, analyzer, tunnel, and
        auto-wire in one sequence.
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

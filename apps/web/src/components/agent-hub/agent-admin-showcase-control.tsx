'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { pauseTradingAgent, resumeTradingAgent, fetchServerBotHealth } from '@/lib/api';

const LAUNCHER = 'http://127.0.0.1:7810';
const DEFAULT_BOT_PORT = 7002;
const DEFAULT_ANALYZER_PORT = 9001;
const FLY_BOT_URL = 'https://doxed-btc-bot.fly.dev';

type HomeStatus = {
  mode?: string;
  stackLabel?: string;
  ports?: { bot?: number; analyzer?: number; launcher?: number };
  bot?: { online?: boolean; ok?: boolean; dashboard?: string; lan?: string; dataDir?: string };
  analyzer?: { online?: boolean; ok?: boolean; dashboard?: string; note?: string };
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
  const [botOk, analyzerOk] = await Promise.all([
    probeLocalHealth(`http://127.0.0.1:${botPort}/api/ping`),
    probeLocalHealth(`http://127.0.0.1:${analyzerPort}/api/status`),
  ]);
  return {
    mode: 'production',
    ports: { bot: botPort, analyzer: analyzerPort, launcher: 7810 },
    bot: { online: botOk, dashboard: `http://127.0.0.1:${botPort}` },
    analyzer: { online: analyzerOk, dashboard: `http://127.0.0.1:${analyzerPort}/` },
  };
}

async function normalizeHomeStatus(raw: HomeStatus & { ok?: boolean }): Promise<HomeStatus> {
  const botPort = raw.ports?.bot ?? DEFAULT_BOT_PORT;
  // Analyzer is always :9001 in the global showcase — ignore stale raw.ports.analyzer.
  const analyzerPort = DEFAULT_ANALYZER_PORT;
  const botDash = raw.bot?.dashboard ?? `http://127.0.0.1:${botPort}`;
  const analyzerDash = raw.analyzer?.dashboard ?? `http://127.0.0.1:${analyzerPort}/`;

  if (raw.ok) {
    return {
      ...raw,
      bot: { ...raw.bot, online: Boolean(raw.bot?.online), dashboard: botDash },
      analyzer: {
        ...raw.analyzer,
        online: Boolean(raw.analyzer?.online),
        dashboard: analyzerDash,
      },
    };
  }

  const botOnline = isOnline(raw.bot);
  const analyzerOnline = isOnline(raw.analyzer);

  const needsBotProbe = !botOnline && raw.bot?.online === undefined && raw.bot?.ok === undefined;
  const needsAnalyzerProbe =
    !analyzerOnline && raw.analyzer?.online === undefined && raw.analyzer?.ok === undefined;

  const [botProbe, analyzerProbe] = await Promise.all([
    needsBotProbe ? probeLocalHealth(`${botDash}/api/ping`) : Promise.resolve(botOnline),
    needsAnalyzerProbe ? probeLocalHealth(`${analyzerDash}api/status`) : Promise.resolve(analyzerOnline),
  ]);

  return {
    ...raw,
    bot: { ...raw.bot, online: botOnline || botProbe, dashboard: botDash },
    analyzer: {
      ...raw.analyzer,
      online: analyzerOnline || analyzerProbe,
      dashboard: analyzerDash,
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
  if (id === 'start-tunnel' || id === 'reset-mirror') return 60_000;
  return INSTANT_CMD_TIMEOUT_MS;
}

/** Desktop observability only. The launcher lock keeps strategy/execution on Fly.io. */
const START_SHOWCASE: HomeCmd = {
  id: 'start-showcase',
  label: '▶ Start desktop tools',
  hint: 'Start the :7002 Fly proxy, Fly data mirror, and desktop analyzer :9001. Fly remains the sole trader.',
  path: '/cmd/start-mirror',
  tone: 'primary',
};

const STOP_SHOWCASE: HomeCmd = {
  id: 'stop-showcase',
  label: '■ Stop desktop tools',
  hint: 'Stop the desktop proxy, analyzer, and optional tunnel. This does not stop the Fly trading owner.',
  path: '/cmd/stop-all-global',
  tone: 'danger',
};

const ADVANCED_COMMANDS: HomeCmd[] = [
  {
    id: 'reset-mirror',
    label: '↻ Reset desktop tools',
    hint: 'Restart the local Fly proxy, data mirror, and analyzer without creating a second trading owner',
    path: '/cmd/reset-mirror',
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

export type FlyStatus = 'online' | 'stale' | 'unreachable';

/**
 * Resolve the Fly strategy/trading owner status without the cross-region
 * flap that affected the old single-probe path.
 *
 * `flyReachable` is the authoritative flag already computed on the dashboard
 * endpoint (trading-agents.service.ts getPublicDashboard) from the canonical
 * snapshot AND the lightweight `/api/ping`+`/health` probe via
 * BotBridgeService.isFlyHealthReachable(). It does NOT flap with the heavy
 * `/api/state` cross-region fetch.
 *
 * `directProbe` is the optional result of the parallel `/bot-health` poll.
 * Because that endpoint probes `/ready` + `/api/ping` (and the live Fly host
 * has been observed timing out on those exact paths while `/health` stays
 * green), `directProbe === false` alone MUST NOT flip the chip to
 * "unreachable" — that is precisely the flap Danish reported. Only when both
 * the authoritative flag AND the direct probe agree Fly is down do we treat
 * it as a true outage.
 */
export function resolveFlyStatus(
  flyReachable?: boolean,
  directProbe?: boolean | null,
): FlyStatus {
  // Either the authoritative dashboard flag OR a successful direct probe is
  // sufficient proof that Fly is up — both are independent connectivity
  // signals that do not depend on the flap-prone heavy /api/state fetch.
  if (flyReachable) return 'online';
  if (directProbe === true) return 'online';

  // Authoritative dashboard flag explicitly says down. This is the same
  // de-flapped signal the public view's relay-sync alert relies on
  // (agent-relay-sync-alerts.ts), so we mirror its semantics: a definitive
  // false is treated as a real outage, never as a transient "stale".
  if (flyReachable === false) return 'unreachable';

  // flyReachable is undefined (dashboard poll hasn't returned yet, e.g. on
  // first render). A lone direct-probe miss here MUST NOT flip the chip to
  // "unreachable" — the live Fly host has been observed timing out /ready +
  // /api/ping while /health stays 200, exactly the flap Danish reported.
  // Surface "stale" until the authoritative flag arrives.
  if (directProbe === false) return 'stale';

  // No data of any kind yet.
  return 'unreachable';
}

/**
 * Resolve the Agent Hub signed-feed status. `botConnected` proves the
 * platform relay holds a canonical snapshot; `flyReachable` proves Fly
 * itself responds to lightweight probes even when that snapshot is briefly
 * stale. Three-state keeps the chip from flapping to "offline" during a
 * transient /api/state fetch miss (the same root cause the public view's
 * relay-sync alert already handles).
 */
export function resolveFeedStatus(
  botConnected?: boolean,
  serverUplinkOnline?: boolean | null,
  flyReachable?: boolean,
): FlyStatus {
  const uplink = serverUplinkOnline ?? botConnected;
  if (uplink) return 'online';
  if (flyReachable) return 'stale';
  return 'unreachable';
}

export function AgentAdminShowcaseControl({
  token,
  executionPaused,
  botConnected,
  flyReachable,
  onUpdated,
}: {
  token: string;
  executionPaused?: boolean;
  botConnected?: boolean;
  /** Authoritative Fly reachability flag from the dashboard endpoint
   *  (snapshot + lightweight probe). Prevents the admin chip from flapping
   *  with the heavy /api/state cross-region fetch that the parallel
   *  /bot-health probe still rides. */
  flyReachable?: boolean;
  onUpdated?: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [execBusy, setExecBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [launcherOnline, setLauncherOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<HomeStatus | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [startSteps, setStartSteps] = useState<string | null>(null);
  const [serverUplinkOnline, setServerUplinkOnline] = useState<boolean | null>(null);
  const [flyDirectProbe, setFlyDirectProbe] = useState<boolean | null>(null);

  const flyStatus = resolveFlyStatus(flyReachable, flyDirectProbe);
  const feedStatus = resolveFeedStatus(botConnected, serverUplinkOnline, flyReachable);

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

  // Server-side signed-feed reachability. `botConnected` proves the platform has
  // a canonical snapshot; it does not by itself prove a direct Fly probe.
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const json = await fetchServerBotHealth('conservative-btc');
        if (!cancelled) {
          setServerUplinkOnline(Boolean(json.botConnected || json.ok));
          // The /bot-health direct probe is a SECONDARY input. Its `fly`
          // field probes /ready + /api/ping, both of which have been
          // observed timing out on the live Fly host while /health (the
          // authoritative flag's path) stays 200. Storing it here lets
          // resolveFlyStatus() treat a lone false as "stale" rather than
          // "unreachable" — the exact flap Danish reported.
          setFlyDirectProbe(json.fly === true ? true : json.fly === false ? false : null);
        }
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
          id === 'reset-mirror'
        ) {
          setTimeout(() => void refreshStatus(), 5000);
          setTimeout(() => void refreshStatus(), 15000);
          setTimeout(() => void refreshStatus(), 45000);
          setTimeout(() => void refreshStatus(), 90000);
        }
        if (id === 'start-showcase' || id === 'reset-mirror') {
          setStartSteps(
            'Starting desktop observability: (1) :7002 compatibility proxy to Fly → (2) incremental Fly data mirror → (3) analyzer :9001. Fly.io remains the only AI, strategy, and trading owner. Refresh status in 30–60s.',
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
      setMsg(message ?? (res.ok ? 'Fly execution state updated' : 'Failed — is the Fly bot reachable?'));
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
        Fly strategy owner + desktop research tools
      </p>
      <p className="mt-1 text-xs text-zinc-400">
        <strong className="text-sky-300">Fly.io is the sole AI, strategy, and trading owner.</strong>{' '}
        This PC is observability only: <strong>:{botPort}</strong> proxies the Fly dashboard,{' '}
        <strong>:{analyzerPort}</strong> analyzes synchronized Fly research data, and <strong>:7810</strong>{' '}
        controls those desktop tools. The Agent Hub and Bitfinex relay receive signed Fly lifecycle events
        through the platform API; the desktop mirror cannot place an independent trade. Open{' '}
        <a href="https://doxxedcrypto.digital/agent-hub/conservative-btc" className="text-violet-300 hover:underline">
          Agent Hub
        </a>{' '}
        on this PC to use the local controls. Platform feed:{' '}
        {botConnected ? (
          <span className="text-emerald-400">online</span>
        ) : (
          <span className="text-red-400">offline</span>
        )}
        .
      </p>

      {launcherOnline === false && (
        <p className="mt-2 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs text-red-200">
          Desktop bridge :7810 is offline — local Start/Stop controls are unavailable. Fly trading is separate
          and may still be online. Run <code className="text-red-100">RESTART-LAUNCHER.cmd</code>, then refresh.
        </p>
      )}

      <div className="mt-3 rounded-lg border border-zinc-700/80 bg-zinc-950/50 px-3 py-2.5 text-xs text-zinc-300">
        <p className="font-semibold text-zinc-200">Desktop observability (optional)</p>
        <p className="mt-1.5 text-zinc-400">
          <strong className="text-zinc-300">Start desktop tools</strong> starts the Fly dashboard proxy, bounded data
          synchronization, and analyzer. <strong className="text-zinc-300">Stop desktop tools</strong> stops only
          those local viewers. The production bot keeps running on Fly when this PC is off.
        </p>
        <p className="mt-2 text-[10px] text-zinc-500">
          Desktop :7002 and :9001 being offline does not mean the Fly bot is offline. Use the Fly/Agent Hub feed
          indicators below for production health.
        </p>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <FlyStatusChip label="Fly strategy/trading owner" status={flyStatus} sub={FLY_BOT_URL} />
        <FlyStatusChip label="Agent Hub signed feed" status={feedStatus} sub="Fly → platform API" />
        <StatusChip
          label={`Desktop Fly proxy :${botPort}`}
          ok={Boolean(status?.bot?.online)}
          sub="Viewer only · no AI or execution"
        />
        <StatusChip
          label={`Desktop analyzer :${analyzerPort}`}
          ok={Boolean(status?.analyzer?.online)}
          sub="Reads synchronized Fly data"
        />
        <StatusChip label="Desktop control bridge :7810" ok={launcherOnline === true} />
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy === START_SHOWCASE.id}
          title={START_SHOWCASE.hint}
          onClick={() => void runLocal(START_SHOWCASE.path, START_SHOWCASE.id)}
          className="min-w-[160px] flex-1 rounded-xl bg-emerald-600 px-5 py-3 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-50 sm:flex-none"
        >
          {busy === START_SHOWCASE.id ? 'Starting…' : START_SHOWCASE.label}
        </button>
        <button
          type="button"
          disabled={busy === STOP_SHOWCASE.id}
          title={STOP_SHOWCASE.hint}
          onClick={() => void runLocal(STOP_SHOWCASE.path, STOP_SHOWCASE.id)}
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
          Desktop Fly mirror :{botPort} →
        </a>
        <a href={analyzerDash} target="_blank" rel="noreferrer" className="text-violet-300 hover:underline">
          Desktop analyzer :{analyzerPort} →
        </a>
        <a href={FLY_BOT_URL} target="_blank" rel="noreferrer" className="text-violet-300 hover:underline">
          Canonical Fly bot →
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
        Production path: Fly strategy owner → signed lifecycle events/snapshots → platform relay → Bitfinex.
        Desktop :7002/:9001 are optional monitoring tools and never health evidence for production.
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
  inactiveLabel,
}: {
  label: string;
  ok: boolean;
  hidden?: boolean;
  sub?: string;
  inactiveLabel?: string;
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
          {label}: <strong>{ok ? 'online' : (inactiveLabel ?? 'offline')}</strong>
        </span>
      </div>
      {sub && <span className="truncate pl-4 text-[9px] text-zinc-600">{sub}</span>}
    </div>
  );
}

/**
 * Three-state status chip for Fly-backed signals. Mirrors the public view's
 * distinction (commit 7a2ce1cf) between:
 *   - online      : fresh proof (snapshot + lightweight probe agree)
 *   - stale (warn): snapshot briefly lagging or direct probe lost a packet,
 *                   but Fly itself is still responding — never flips to the
 *                   scary red "offline" label on a transient miss
 *   - unreachable : true outage — authoritative flag AND direct probe both
 *                   failed
 */
function FlyStatusChip({
  label,
  status,
  sub,
}: {
  label: string;
  status: FlyStatus;
  sub?: string;
}) {
  const tone =
    status === 'online'
      ? 'border-emerald-500/40 text-emerald-300'
      : status === 'stale'
        ? 'border-amber-500/40 text-amber-300'
        : 'border-red-500/50 text-red-300';
  const dotClass =
    status === 'online'
      ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]'
      : status === 'stale'
        ? 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.7)]'
        : 'bg-red-500';
  const labelText =
    status === 'online' ? 'online' : status === 'stale' ? 'feed stale' : 'unreachable';
  return (
    <div className={`flex flex-col gap-0.5 rounded-lg border px-2 py-1.5 text-[11px] ${tone}`}>
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${dotClass}`}
          title={labelText}
          aria-hidden
        />
        <span>
          {label}: <strong>{labelText}</strong>
        </span>
      </div>
      {sub && <span className="truncate pl-4 text-[9px] text-zinc-600">{sub}</span>}
    </div>
  );
}

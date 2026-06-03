'use client';

import { useCallback, useEffect, useState } from 'react';
import type { BuilderSettings } from '@/lib/api';

/** Founder Node v0.5.0+ required for sync jobs / vector rebuild */
function founderNodeNeedsUpdate(version: string | null | undefined): boolean {
  if (!version?.trim()) return true;
  const m = version.trim().match(/^v?(\d+)\.(\d+)/i);
  if (!m) return true;
  const major = Number(m[1]);
  const minor = Number(m[2]);
  return major < 0 || (major === 0 && minor < 5);
}

function formatLastSeen(iso: string | null | undefined): string {
  if (!iso) return 'never (no heartbeat yet)';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return 'just now';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  return new Date(iso).toLocaleString();
}
import {
  pushGoalToFounderNode,
  runFounderNodeAgent,
  searchFounderVault,
} from '@/lib/api';

type Props = {
  accessToken: string;
  settings: BuilderSettings;
  onRefresh: () => void;
  embedded?: boolean;
};

export function FounderNodeV2Panel({ accessToken, settings, onRefresh, embedded }: Props) {
  const v2 = settings.founderNodeV2;
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hits, setHits] = useState<Array<{ source: string; text: string; score: number }>>([]);

  const isFounderNodeMode = settings.memoryStorageMode === 'FOUNDER_NODE';

  useEffect(() => {
    if (!v2?.paired || v2.online) return;
    const timer = setInterval(() => onRefresh(), 12_000);
    return () => clearInterval(timer);
  }, [v2?.paired, v2?.online, onRefresh]);

  const runSearch = useCallback(async () => {
    if (!query.trim()) return;
    setBusy('search');
    setErr(null);
    setMsg(null);
    try {
      const result = await searchFounderVault(query.trim(), accessToken);
      const nextHits = result.hits ?? [];
      setHits(nextHits);
      setMsg(nextHits.length ? `Found ${nextHits.length} local vault match(es)` : 'No matches in local vault index');
      onRefresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Vault search failed');
    } finally {
      setBusy(null);
    }
  }, [accessToken, onRefresh, query]);

  async function runAgent(agent: 'vault-index' | 'goal-align' | 'vault-summary') {
    setBusy(agent);
    setErr(null);
    setMsg(null);
    try {
      const result = await runFounderNodeAgent(agent, accessToken, {
        goal: settings.currentGoalFocus ?? undefined,
        query: query.trim() || undefined,
      });
      setMsg(typeof result.summary === 'string' ? result.summary : `${agent} completed on Founder Node`);
      onRefresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Agent run failed');
    } finally {
      setBusy(null);
    }
  }

  async function pushGoal() {
    const goal = settings.currentGoalFocus?.trim();
    if (!goal) {
      setErr('Set a current goal focus first');
      return;
    }
    setBusy('push-goal');
    setErr(null);
    try {
      await pushGoalToFounderNode(goal, accessToken);
      setMsg('Goal queued for your Founder Node vault');
      onRefresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Push goal failed');
    } finally {
      setBusy(null);
    }
  }

  if (!isFounderNodeMode && !v2?.paired) {
    return embedded ? (
      <p className="text-sm text-zinc-500">
        Enable <strong className="text-zinc-300">Founder Vault (Founder Node)</strong> in Step 2 above, then pair
        your desktop app.
      </p>
    ) : null;
  }

  const inner = (
    <>
      {!embedded && (
        <>
          <h2 className="text-lg font-semibold text-white">Founder Node v2 (Step 4)</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Local vector index, bidirectional sync, and on-device agents — cloud pushes goals; your vault stays on the desktop.
          </p>
        </>
      )}

      <div className={`flex flex-wrap gap-2 text-[10px] font-semibold ${embedded ? 'mt-0' : 'mt-4'}`}>
        {v2?.paired ? (
          <span className="rounded-full bg-cyan-500/20 px-2.5 py-1 text-cyan-100">
            Node paired{v2.nodeLabel ? ` · ${v2.nodeLabel}` : ''}
          </span>
        ) : (
          <span className="rounded-full bg-amber-500/20 px-2.5 py-1 text-amber-100">Pair Founder Node first</span>
        )}
        {v2?.online ? (
          <span className="rounded-full bg-emerald-500/20 px-2.5 py-1 text-emerald-200">● online</span>
        ) : (
          <span className="rounded-full bg-zinc-700/60 px-2.5 py-1 text-zinc-300">○ offline</span>
        )}
        {typeof v2?.vectorChunks === 'number' && v2.vectorChunks > 0 && (
          <span className="rounded-full bg-violet-500/20 px-2.5 py-1 text-violet-100">
            {v2.vectorChunks} indexed chunks
          </span>
        )}
        {(v2?.pendingJobs ?? 0) > 0 && (
          <span className="rounded-full bg-amber-500/20 px-2.5 py-1 text-amber-100">
            {v2?.pendingJobs} pending sync job(s)
          </span>
        )}
        {v2?.appVersion && (
          <span className="rounded-full bg-zinc-700/60 px-2.5 py-1 text-zinc-300">v{v2.appVersion}</span>
        )}
      </div>

      {v2?.paired && v2.online && founderNodeNeedsUpdate(v2.appVersion) && (
        <div className="mt-4 rounded-xl border border-amber-500/35 bg-amber-950/25 p-4 text-sm text-amber-100">
          <p className="font-medium">Update Founder Node to v0.5.0+</p>
          <p className="mt-1 text-xs text-zinc-400">
            Your tray app{v2.appVersion ? ` (v${v2.appVersion})` : ''} cannot process sync jobs. Download the latest
            installer in <strong className="text-cyan-200">Step 1 above</strong>, install it, then restart and retry
            Rebuild vector index.
          </p>
        </div>
      )}

      {!v2?.paired && !embedded && (
        <p className="mt-4 text-sm text-zinc-400">
          Enable <strong>Founder Vault (Founder Node)</strong> in Step 2 and install the tray app from Step 1.
        </p>
      )}

      {!v2?.paired && (
        <p className="mt-3 text-xs text-amber-200/90">
          Complete <strong className="text-amber-100">Step 2</strong> first — generate a pairing code and enter it in the
          Founder Node tray app on this PC.
        </p>
      )}

      {v2?.paired && !v2.online && (
        <div className="mt-4 rounded-xl border border-amber-500/40 bg-amber-950/25 p-4 text-sm text-amber-100">
          <p className="font-semibold text-amber-200">Paired on Founder OS, but desktop app is offline</p>
          <p className="mt-2 text-xs leading-relaxed text-zinc-300">
            <strong className="text-white">Paired</strong> means this browser account is linked to your machine (one-time
            setup). <strong className="text-white">Online</strong> means the Founder Node tray app is running on your PC
            and sending a heartbeat to Founder OS — usually every ~60 seconds. The website cannot index your vault by
            itself; <strong className="text-white">Rebuild vector index</strong> is disabled until the tray app is online.
          </p>
          <p className="mt-2 text-[11px] text-zinc-500">
            Last heartbeat from your node: <span className="text-zinc-400">{formatLastSeen(v2.lastSeenAt)}</span>
            {v2.nodeLabel ? (
              <>
                {' '}
                · machine: <span className="text-zinc-400">{v2.nodeLabel}</span>
              </>
            ) : null}
            . This page refreshes every 12s while offline.
          </p>

          <div className="mt-4 rounded-lg border border-amber-500/25 bg-black/20 p-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-amber-200/90">
              Get online (about 1 minute)
            </p>
            <ol className="mt-2 list-decimal space-y-2 pl-5 text-xs leading-relaxed text-zinc-300">
              <li>
                Open <strong className="text-white">Founder Node</strong> from the Windows Start Menu (search “Founder
                Node”). Do not use <strong className="text-white">Quit</strong> on the tray menu — that stops heartbeats.
              </li>
              <li>
                Find the tray icon near the clock (bottom-right). If you do not see it, click the{' '}
                <strong className="text-white">^</strong> arrow to show hidden icons.
              </li>
              <li>
                Right-click the Founder Node tray icon → <strong className="text-white">Sync now</strong>. Wait until the
                badge here shows <strong className="text-emerald-300">● online</strong> (within ~3 minutes of the last
                heartbeat).
              </li>
              <li>
                Then click <strong className="text-white">Rebuild vector index</strong> once (first run can take 30–60s).
              </li>
            </ol>
          </div>

          <div className="mt-3 space-y-1.5 text-[11px] text-zinc-500">
            <p>
              <strong className="text-zinc-400">Closing the pairing popup is OK.</strong> “Pair this machine” is only for
              entering your code. After pairing, only the tray app needs to stay open — not the popup window.
            </p>
            <p>
              <strong className="text-zinc-400">Still offline?</strong> Check Windows Firewall allowed Founder Node,
              sign in to the same Founder OS account you paired with, and confirm Step 2 uses{' '}
              <strong className="text-zinc-300">Founder Vault (Founder Node)</strong>. Restart the tray app, then Sync
              now again.
            </p>
          </div>
        </div>
      )}

      {v2?.paired && v2.online && (
        <p className="mt-3 text-xs text-emerald-300/90">
          Desktop app is online — sync jobs run on your machine. Vault files never leave your PC unless you search (only
          snippets return).
        </p>
      )}

      {v2?.paired && (
        <div className="mt-6 space-y-4">
          <div className="rounded-xl border border-cyan-500/25 bg-cyan-950/20 p-4">
            <p className="text-sm font-medium text-cyan-100">After pairing — do this next</p>
            <ol className="mt-2 space-y-1.5 text-xs text-zinc-400">
              <li className={v2.online ? 'text-emerald-300' : 'text-amber-200'}>
                {v2.online ? '✓' : '○'} Keep Founder Node tray app open on your desktop (popup can close; tray stays)
              </li>
              <li className={(v2.vectorChunks ?? 0) > 0 ? 'text-emerald-300' : 'text-zinc-300'}>
                {(v2.vectorChunks ?? 0) > 0 ? '✓' : '○'} Click <strong className="font-medium">Rebuild vector index</strong>{' '}
                once (indexes vault for search — Step 5 needs this)
              </li>
              <li className="text-zinc-400">
                ○ Wait a few minutes for encrypted backup sync (Step 5 “encrypted relay” check)
              </li>
              <li className="text-zinc-400">
                ○ Optional: set a goal focus above, then <strong className="font-medium">Push goal to vault</strong>
              </li>
            </ol>
          </div>

          {!v2.online && (
            <p className="text-[11px] text-zinc-500">
              Buttons below unlock when the tray app is <strong className="text-zinc-400">● online</strong>.
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-3">
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online}
              title={
                v2.online
                  ? 'Queue index rebuild on your desktop (vault-index agent)'
                  : 'Start Founder Node tray app and wait for ● online'
              }
              onClick={() => runAgent('vault-index')}
              className="rounded-lg border border-cyan-500/40 bg-cyan-950/30 px-3 py-2 text-sm text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Rebuild vector index
            </button>
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online}
              title={v2.online ? undefined : 'Requires desktop app online'}
              onClick={() => runAgent('goal-align')}
              className="rounded-lg border border-cyan-500/40 bg-cyan-950/30 px-3 py-2 text-sm text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Check goal alignment
            </button>
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online || !settings.currentGoalFocus?.trim()}
              title={
                !v2.online
                  ? 'Requires desktop app online'
                  : !settings.currentGoalFocus?.trim()
                    ? 'Set current goal focus in Step 3 first'
                    : undefined
              }
              onClick={pushGoal}
              className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-3 py-2 text-sm text-violet-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Push goal to vault
            </button>
          </div>

          <div>
            <label className="block text-sm text-zinc-400">Search local vault (semantic index on your machine)</label>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="roadmap launch partnership…"
                className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
              />
              <button
                type="button"
                disabled={Boolean(busy) || !v2.online || !query.trim()}
                onClick={runSearch}
                className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                Search vault
              </button>
            </div>
          </div>

          {hits.length > 0 && (
            <ul className="space-y-2 text-xs text-zinc-300">
              {hits.map((hit, i) => (
                <li key={`${hit.source}-${i}`} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                  <p className="font-medium text-cyan-200">
                    {hit.source} · score {hit.score.toFixed(2)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-zinc-400">{hit.text.slice(0, 280)}</p>
                </li>
              ))}
            </ul>
          )}

          {v2.lastPullSyncAt && (
            <p className="text-[11px] text-zinc-600">
              Last bidirectional sync {new Date(v2.lastPullSyncAt).toLocaleString()}
              {v2.vectorIndexedAt ? ` · index ${new Date(v2.vectorIndexedAt).toLocaleString()}` : ''}
            </p>
          )}
        </div>
      )}

      {msg && <p className="mt-3 text-sm text-emerald-300">{msg}</p>}
      {err && (
        <div className="mt-3 space-y-2">
          <p className="text-sm text-red-400">{err}</p>
          {err.toLowerCase().includes('timed out') && (
            <div className="rounded-lg border border-red-500/25 bg-red-950/20 p-3 text-xs text-zinc-400">
              <p className="font-medium text-red-200">Founder Node did not respond in time</p>
              <ul className="mt-2 list-disc space-y-1 pl-4">
                <li>Confirm the tray app shows connected (not just “paired” in the browser)</li>
                <li>Restart Founder Node, then try Rebuild vector index again</li>
                <li>On Windows, allow Founder Node through firewall if prompted</li>
                <li>First index build can take 30–60s — keep the app in the foreground</li>
              </ul>
            </div>
          )}
        </div>
      )}
    </>
  );

  if (embedded) return <div>{inner}</div>;

  return (
    <section className="rounded-2xl border border-cyan-500/35 bg-cyan-950/10 p-6">{inner}</section>
  );
}

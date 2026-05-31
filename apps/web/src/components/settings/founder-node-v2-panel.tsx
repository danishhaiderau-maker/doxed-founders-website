'use client';

import { useCallback, useState } from 'react';
import Link from 'next/link';
import type { BuilderSettings } from '@/lib/api';
import {
  pushGoalToFounderNode,
  runFounderNodeAgent,
  searchFounderVault,
} from '@/lib/api';

type Props = {
  accessToken: string;
  settings: BuilderSettings;
  onRefresh: () => void;
};

export function FounderNodeV2Panel({ accessToken, settings, onRefresh }: Props) {
  const v2 = settings.founderNodeV2;
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hits, setHits] = useState<Array<{ source: string; text: string; score: number }>>([]);

  const isFounderNodeMode = settings.memoryStorageMode === 'FOUNDER_NODE';

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
    return null;
  }

  return (
    <section className="rounded-2xl border border-cyan-500/35 bg-cyan-950/10 p-6">
      <h2 className="text-lg font-semibold text-white">Founder Node v2 (Step 4)</h2>
      <p className="mt-1 text-sm text-zinc-500">
        Local vector index, bidirectional sync, and on-device agents — cloud pushes goals; your vault stays on the desktop.
      </p>

      <div className="mt-4 flex flex-wrap gap-2 text-[10px] font-semibold">
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
      </div>

      {!v2?.paired && (
        <p className="mt-4 text-sm text-zinc-400">
          Enable <strong>Founder Vault (Founder Node)</strong> above and install the tray app from{' '}
          <Link href="/founder-node" className="text-cyan-300 underline">
            /founder-node
          </Link>
          .
        </p>
      )}

      {v2?.paired && (
        <div className="mt-6 space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online}
              onClick={() => runAgent('vault-index')}
              className="rounded-lg border border-cyan-500/40 bg-cyan-950/30 px-3 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              Rebuild vector index
            </button>
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online}
              onClick={() => runAgent('goal-align')}
              className="rounded-lg border border-cyan-500/40 bg-cyan-950/30 px-3 py-2 text-sm text-cyan-100 disabled:opacity-50"
            >
              Check goal alignment
            </button>
            <button
              type="button"
              disabled={Boolean(busy) || !v2.online || !settings.currentGoalFocus?.trim()}
              onClick={pushGoal}
              className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-3 py-2 text-sm text-violet-100 disabled:opacity-50"
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
      {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
    </section>
  );
}

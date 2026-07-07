'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { apiUrl } from '@/lib/api-base';
import { RefreshCw } from 'lucide-react';

type RoutingDecision = {
  id: string;
  requestId: string;
  intent: string;
  profile: string;
  chosenProvider: string;
  chosenModel: string;
  cacheLevel: string;
  latencyMs: number | null;
  costUsd: number | null;
  tokenCountPrompt: number | null;
  tokenCountCompletion: number | null;
  createdAt: string;
};

export default function DecisionLogPage() {
  const { data: session } = useSession();
  const [decisions, setDecisions] = useState<RoutingDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!session?.accessToken) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(apiUrl('/api/flight-recorder/recent?limit=50'), {
        headers: { Authorization: `Bearer ${session.accessToken}` },
      });
      if (!res.ok) {
        setErr(`Failed to load (${res.status})`);
        setDecisions([]);
      } else {
        const data = (await res.json()) as RoutingDecision[];
        setDecisions(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [session?.accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Decision Log</h1>
            <p className="text-xs text-zinc-500">
              Every routing decision the kernel makes — the Flight Recorder.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-4 flex items-center justify-between">
          <Link href="/founder-os" className="text-xs text-zinc-500 hover:text-white">
            ← Founder OS
          </Link>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:border-zinc-500 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        {!session?.accessToken ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
            <Link href="/login?callbackUrl=/founder-os/decisions" className="font-semibold underline">
              Sign in
            </Link>{' '}
            to view your routing decisions.
          </div>
        ) : err ? (
          <div className="rounded-xl border border-rose-500/30 bg-rose-950/15 p-6 text-sm text-rose-100">
            {err}
          </div>
        ) : decisions.length === 0 && !loading ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-8 text-center text-sm text-zinc-500">
            No routing decisions yet. Once the AI Proxy goes live, every request
            it routes will appear here.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-zinc-800">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-zinc-800 bg-zinc-950 text-[11px] uppercase tracking-wider text-zinc-500">
                <tr>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Intent</th>
                  <th className="px-3 py-2">Profile</th>
                  <th className="px-3 py-2">Routed to</th>
                  <th className="px-3 py-2">Cache</th>
                  <th className="px-3 py-2 text-right">Latency</th>
                  <th className="px-3 py-2 text-right">Tokens</th>
                  <th className="px-3 py-2 text-right">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-900">
                {decisions.map((d) => (
                  <tr key={d.id} className="hover:bg-zinc-950/60">
                    <td className="px-3 py-2 text-zinc-400">
                      {new Date(d.createdAt).toLocaleString(undefined, {
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        month: 'short',
                        day: 'numeric',
                      })}
                    </td>
                    <td className="px-3 py-2">
                      <IntentBadge intent={d.intent} />
                    </td>
                    <td className="px-3 py-2 text-zinc-400">{d.profile}</td>
                    <td className="px-3 py-2 font-mono text-zinc-200">
                      <span className="text-zinc-500">{d.chosenProvider}/</span>
                      {d.chosenModel}
                    </td>
                    <td className="px-3 py-2">
                      <CacheBadge level={d.cacheLevel} />
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-400">
                      {d.latencyMs != null ? `${d.latencyMs}ms` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-400">
                      {(d.tokenCountPrompt ?? 0) + (d.tokenCountCompletion ?? 0) || '—'}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-400">
                      {d.costUsd != null
                        ? `$${d.costUsd.toFixed(4)}`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}

function IntentBadge({ intent }: { intent: string }) {
  const color =
    intent === 'code'
      ? 'border-sky-700 bg-sky-950/40 text-sky-200'
      : intent === 'reasoning'
        ? 'border-violet-700 bg-violet-950/40 text-violet-200'
        : intent === 'simple_qa'
          ? 'border-emerald-700 bg-emerald-950/40 text-emerald-200'
          : 'border-zinc-700 bg-zinc-900 text-zinc-300';
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${color}`}>
      {intent}
    </span>
  );
}

function CacheBadge({ level }: { level: string }) {
  const color =
    level === 'hit'
      ? 'border-emerald-700 bg-emerald-950/40 text-emerald-200'
      : level === 'partial'
        ? 'border-amber-700 bg-amber-950/40 text-amber-200'
        : 'border-zinc-700 bg-zinc-900 text-zinc-500';
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${color}`}>
      {level}
    </span>
  );
}

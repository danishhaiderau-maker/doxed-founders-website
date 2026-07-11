'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import { useSession } from 'next-auth/react';

type SuggestedFix = {
  title: string;
  fix: string;
  severity: 'low' | 'medium' | 'high';
  files: string[];
};

type RunSummary = {
  runId: string;
  triggeredBy: 'cron' | 'manual' | 'startup';
  startedAt: string;
  durationMs: number;
  overall: 'PASS' | 'FAIL' | 'DEGRADED';
  readinessScore: number;
  totals: { checksRun: number; checksPassed: number; checksFailed: number };
  pillars: Array<{
    pillar: string;
    status: string;
    summary: string;
    diagnosis: string | null;
    suggestedFixes: SuggestedFix[];
    runDurationMs: number;
  }>;
};

type HistoryEntry = {
  id: string;
  createdAt: string;
  status: string;
  summary: string;
  triggeredBy: string;
  durationMs: number;
};

/**
 * Full admin panel for the Debug Squasher.
 *
 * Surfaces:
 *   - Latest run with per-pillar breakdown + AI diagnoses
 *   - Run history (last 20 overall rows)
 *   - "Trigger run now" button (POST /api/debug-squasher/run — admin only)
 *
 * Used by apps/web/src/app/admin/debug-squasher/page.tsx.
 */
export function DebugSquasherPanel() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  const [latest, setLatest] = useState<RunSummary | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [latestRes, histRes] = await Promise.all([
        fetch(apiUrl('/api/debug-squasher/latest'), {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(apiUrl('/api/debug-squasher/history?limit=20'), {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ]);
      if (latestRes.ok) {
        const data = (await latestRes.json()) as { run: RunSummary | null };
        setLatest(data.run);
      }
      if (histRes.ok) {
        const data = (await histRes.json()) as { history: HistoryEntry[] };
        setHistory(data.history);
      } else if (histRes.status === 403) {
        setError('Admin access required to view run history.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load debug-squasher state.');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (status === 'authenticated') void load();
  }, [status, load]);

  async function triggerRun() {
    if (!token) return;
    setBusy(true);
    setMsg(null);
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/debug-squasher/run'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(txt || `Run failed (${res.status})`);
      }
      const data = (await res.json()) as { run: RunSummary };
      setLatest(data.run);
      setMsg('Run complete.');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Run failed.');
    } finally {
      setBusy(false);
    }
  }

  if (status === 'loading' || loading) {
    return <p className="text-sm text-zinc-400">Loading Debug Squasher…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Debug Squasher</h1>
          <p className="text-xs text-zinc-500">
            Daily health check + AI bug diagnostician. Phase 6.5.
          </p>
        </div>
        {isAdmin && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void triggerRun()}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? 'Running…' : 'Trigger run now'}
          </button>
        )}
      </div>

      {msg && (
        <div className="rounded-md border border-emerald-900/60 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">
          {msg}
        </div>
      )}
      {error && (
        <div className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <LatestRunSection latest={latest} />

      <HistorySection history={history} />
    </div>
  );
}

function LatestRunSection({ latest }: { latest: RunSummary | null }) {
  if (!latest) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
        <h2 className="mb-2 text-sm font-semibold text-zinc-200">Latest run</h2>
        <p className="text-sm text-zinc-400">No runs yet. Trigger one above.</p>
      </section>
    );
  }

  const failedPillars = latest.pillars.filter((p) => p.status !== 'pass');
  const overallOk = latest.overall === 'PASS';

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">Latest run</h2>
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
            overallOk
              ? 'bg-emerald-950/60 text-emerald-300'
              : latest.overall === 'DEGRADED'
                ? 'bg-amber-950/60 text-amber-300'
                : 'bg-red-950/60 text-red-300'
          }`}
        >
          {latest.overall}
        </span>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Stat label="Score" value={`${latest.readinessScore}/100`} />
        <Stat label="Checks" value={`${latest.totals.checksPassed}/${latest.totals.checksRun}`} />
        <Stat label="Duration" value={`${(latest.durationMs / 1000).toFixed(1)}s`} />
        <Stat label="Started" value={new Date(latest.startedAt).toLocaleString()} />
      </div>

      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        Pillar breakdown
      </h3>
      <div className="space-y-2">
        {latest.pillars.map((p) => (
          <PillarRow key={p.pillar} pillar={p} />
        ))}
      </div>

      {failedPillars.length > 0 && (
        <>
          <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            AI diagnoses ({failedPillars.length})
          </h3>
          <div className="space-y-3">
            {failedPillars.map((p) => (
              <DiagnosisCard key={p.pillar} pillar={p} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function PillarRow({ pillar }: { pillar: RunSummary['pillars'][number] }) {
  const color =
    pillar.status === 'pass'
      ? 'text-emerald-300'
      : pillar.status === 'degraded'
        ? 'text-amber-300'
        : 'text-red-300';
  return (
    <div className="flex items-center justify-between border-b border-zinc-900 py-1.5 text-sm">
      <span className={`font-medium ${color}`}>
        {pillar.status === 'pass' ? '✓' : '⚠'} {pillar.pillar}
      </span>
      <span className="text-xs text-zinc-500">{pillar.summary}</span>
    </div>
  );
}

function DiagnosisCard({ pillar }: { pillar: RunSummary['pillars'][number] }) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/50 p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-semibold text-red-300">{pillar.pillar}</span>
        <span className="text-[11px] text-zinc-500">{pillar.summary}</span>
      </div>
      {pillar.diagnosis && (
        <p className="mb-3 text-xs leading-relaxed text-zinc-300">{pillar.diagnosis}</p>
      )}
      {pillar.suggestedFixes.length > 0 && (
        <ul className="space-y-1.5">
          {pillar.suggestedFixes.map((fix, idx) => {
            const copyKey = `${pillar.pillar}-${idx}`;
            return (
              <li key={copyKey} className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-zinc-200">
                    <span
                      className={`mr-1.5 inline-block rounded px-1 text-[10px] uppercase ${
                        fix.severity === 'high'
                          ? 'bg-red-950/60 text-red-300'
                          : fix.severity === 'medium'
                            ? 'bg-amber-950/60 text-amber-300'
                            : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {fix.severity}
                    </span>
                    {fix.title}
                  </p>
                  <p className="text-xs text-zinc-400">{fix.fix}</p>
                  {fix.files.length > 0 && (
                    <p className="mt-0.5 text-[11px] text-zinc-500">
                      Files: {fix.files.join(', ')}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard.writeText(fix.fix);
                    setCopiedKey(copyKey);
                    setTimeout(() => setCopiedKey(null), 1500);
                  }}
                  className="shrink-0 rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-300 transition hover:border-zinc-600 hover:text-zinc-100"
                >
                  {copiedKey === copyKey ? 'Copied!' : 'Copy fix'}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function HistorySection({ history }: { history: HistoryEntry[] }) {
  if (history.length === 0) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
        <h2 className="mb-2 text-sm font-semibold text-zinc-200">Run history</h2>
        <p className="text-sm text-zinc-400">No runs recorded yet.</p>
      </section>
    );
  }
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
      <h2 className="mb-3 text-sm font-semibold text-zinc-200">Run history (last 20)</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500">
              <th className="py-2 pr-3">Started</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Summary</th>
              <th className="py-2 pr-3">Trigger</th>
              <th className="py-2">Duration</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id} className="border-b border-zinc-900">
                <td className="py-2 pr-3 text-zinc-300">
                  {new Date(h.createdAt).toLocaleString()}
                </td>
                <td className="py-2 pr-3">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                      h.status === 'pass'
                        ? 'bg-emerald-950/60 text-emerald-300'
                        : h.status === 'degraded'
                          ? 'bg-amber-950/60 text-amber-300'
                          : 'bg-red-950/60 text-red-300'
                    }`}
                  >
                    {h.status}
                  </span>
                </td>
                <td className="py-2 pr-3 text-zinc-400">{h.summary}</td>
                <td className="py-2 pr-3 text-zinc-400">{h.triggeredBy}</td>
                <td className="py-2 text-zinc-400">{(h.durationMs / 1000).toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2">
      <div className="text-sm font-semibold text-zinc-100">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
    </div>
  );
}

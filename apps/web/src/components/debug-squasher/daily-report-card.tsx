'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
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

/**
 * Daily Report card for the Founder OS shell.
 *
 * Polls GET /api/debug-squasher/latest every 60s and shows:
 *   - Overall platform verdict (healthy / N issues found)
 *   - Per-pillar pass/fail counts
 *   - For each failed pillar: diagnosis and a copyable suggested fix
 *
 * Designed to sit next to the existing observability widgets in the shell.
 */
export function DailyReportCard() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [run, setRun] = useState<RunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(apiUrl('/api/debug-squasher/latest'), {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = (await res.json()) as { run: RunSummary | null };
        setRun(data.run);
      }
    } catch {
      // surfaced by the empty state below
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 60_000);
    return () => clearInterval(id);
  }, [load]);

  const failedPillars = run?.pillars.filter((p) => p.status !== 'pass') ?? [];

  if (loading) {
    return (
      <Card>
        <p className="text-sm text-zinc-400">Loading the latest quality review...</p>
      </Card>
    );
  }

  if (!run) {
    return (
      <Card>
        <p className="text-sm text-zinc-400">No daily quality review is available yet.</p>
        <Link
          href="/admin/debug-squasher"
          className="mt-2 inline-block text-xs text-emerald-400 hover:underline"
        >
          Run a review from the admin panel
        </Link>
      </Card>
    );
  }

  const overallOk = run.overall === 'PASS';

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Daily quality review
        </h3>
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
            overallOk
              ? 'bg-emerald-950/60 text-emerald-300'
              : run.overall === 'DEGRADED'
                ? 'bg-amber-950/60 text-amber-300'
                : 'bg-red-950/60 text-red-300'
          }`}
        >
          {overallOk ? 'Healthy' : `${run.totals.checksFailed} issues found`}
        </span>
      </div>

      <div className="mb-4 grid grid-cols-3 gap-2 text-center text-sm">
        <Stat label="Readiness" value={`${run.readinessScore}/100`} />
        <Stat label="Checks passed" value={`${run.totals.checksPassed}/${run.totals.checksRun}`} />
        <Stat label="Duration" value={`${(run.durationMs / 1000).toFixed(1)}s`} />
      </div>

      {failedPillars.length === 0 ? (
        <p className="text-sm text-emerald-300">
          All {run.pillars.length} pillars green. Last run{' '}
          {new Date(run.startedAt).toLocaleString()}.
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-zinc-400">
            {failedPillars.length} pillar{failedPillars.length === 1 ? '' : 's'} failed on{' '}
            {new Date(run.startedAt).toLocaleString()}:
          </p>
          {failedPillars.map((p) => (
            <PillarDiagnosis
              key={p.pillar}
              pillar={p}
              copiedKey={copiedKey}
              onCopy={(key) => setCopiedKey(key)}
            />
          ))}
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-xs">
        <span className="text-zinc-500">Triggered by {run.triggeredBy}</span>
        <Link href="/admin/debug-squasher" className="text-emerald-400 hover:underline">
          View history
        </Link>
      </div>
    </Card>
  );
}

function PillarDiagnosis({
  pillar,
  copiedKey,
  onCopy,
}: {
  pillar: RunSummary['pillars'][number];
  copiedKey: string | null;
  onCopy: (key: string) => void;
}) {
  const severityColor =
    pillar.status === 'fail'
      ? 'text-red-300'
      : pillar.status === 'degraded'
        ? 'text-amber-300'
        : 'text-zinc-300';

  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/50 p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className={`text-sm font-medium ${severityColor}`}>{pillar.pillar}</span>
        <span className="text-[11px] text-zinc-500">{pillar.summary}</span>
      </div>

      {pillar.diagnosis && (
        <p className="mb-2 text-xs leading-relaxed text-zinc-400">{pillar.diagnosis}</p>
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
                  <p className="truncate text-xs text-zinc-400" title={fix.fix}>
                    {fix.fix}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard.writeText(fix.fix);
                    onCopy(copyKey);
                    setTimeout(() => onCopy(''), 1500);
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-2 py-2">
      <div className="text-sm font-semibold text-zinc-100">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
      {children}
    </section>
  );
}

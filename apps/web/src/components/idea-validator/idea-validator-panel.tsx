'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import {
  VERDICT_META,
  type IdeaCheck,
  type IdeaValidationReport,
} from './types';

type Props = { accessToken: string };

/**
 * IdeaValidatorPanel — the main Founder Idea Validator surface.
 *
 * Shows: the idea input, the "Check my idea" button, the loading state
 * (browsing… analyzing…), and the results: competitive landscape table,
 * differentiation score gauge, similar projects, suggested OSS, and the
 * one-paragraph summary. Polls the API until the async check completes.
 */
export function IdeaValidatorPanel({ accessToken }: Props) {
  const [ideaText, setIdeaText] = useState('');
  const [activeCheck, setActiveCheck] = useState<IdeaCheck | null>(null);
  const [history, setHistory] = useState<IdeaCheck[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/idea-validator/checks'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) {
        const rows = (await res.json()) as IdeaCheck[];
        setHistory(rows);
      }
    } catch {
      // surfaced by empty state
    }
  }, [accessToken]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  // Poll the active check until it reaches a terminal state.
  useEffect(() => {
    if (!activeCheck) return;
    if (activeCheck.status === 'COMPLETED' || activeCheck.status === 'FAILED') return;
    const id = activeCheck.id;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/idea-validator/check/${id}`), {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.ok) {
          const row = (await res.json()) as IdeaCheck;
          setActiveCheck(row);
          if (row.status === 'COMPLETED' || row.status === 'FAILED') {
            clearInterval(timer);
            void loadHistory();
          }
        }
      } catch {
        // keep polling
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [activeCheck, accessToken, loadHistory]);

  const submit = useCallback(async () => {
    if (ideaText.trim().length < 20) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/idea-validator/check'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ideaText: ideaText.trim() }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(`${res.status}: ${txt.slice(0, 160)}`);
      }
      const row = (await res.json()) as IdeaCheck;
      setActiveCheck(row);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [accessToken, ideaText]);

  const isLoading =
    !!activeCheck &&
    (activeCheck.status === 'PENDING' || activeCheck.status === 'RUNNING');
  const report = activeCheck?.resultJson ?? null;

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <h2 className="text-base font-semibold text-white">Founder Idea Validator</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Describe your idea. Founder OS browses GitHub + the web for similar projects, then returns a
          competitive landscape report with a differentiation score and reusable open-source suggestions.
        </p>
        <textarea
          value={ideaText}
          onChange={(e) => setIdeaText(e.target.value)}
          placeholder="e.g. I want to build real-time alerts for retail traders so they don't get frontrun on Solana mempools…"
          className="mt-4 h-28 w-full resize-none rounded-lg border border-zinc-800 bg-black/40 p-3 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-500"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={submit}
            disabled={ideaText.trim().length < 20 || submitting || isLoading}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? 'Starting…' : 'Check my idea'}
          </button>
          <span className="text-xs text-zinc-600">
            {ideaText.trim().length < 20 ? 'At least 20 characters' : `${ideaText.trim().length} chars`}
          </span>
        </div>
        {error && <p className="mt-3 text-xs text-rose-400">{error}</p>}
      </div>

      {isLoading && <LoadingState status={activeCheck?.status ?? 'PENDING'} />}

      {activeCheck?.status === 'FAILED' && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-4 text-sm text-rose-200">
          Check failed: {activeCheck.errorMessage ?? 'unknown error'}
        </div>
      )}

      {report && activeCheck?.status === 'COMPLETED' && (
        <ReportView report={report} ideaText={activeCheck.ideaText} />
      )}

      {history.length > 0 && !isLoading && (
        <HistoryList
          history={history}
          accessToken={accessToken}
          onSelect={(row) => setActiveCheck(row)}
        />
      )}
    </div>
  );
}

function LoadingState({ status }: { status: string }) {
  const steps = [
    { label: 'Extracting keywords from your description', done: status === 'RUNNING' },
    { label: 'Searching GitHub + the web for similar projects', done: false },
    { label: 'Synthesizing competitive analysis', done: false },
  ];
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
      <div className="flex items-center gap-2 text-sm text-zinc-300">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-violet-400" />
        Checking the landscape for your idea…
      </div>
      <ul className="mt-4 space-y-2">
        {steps.map((s, i) => (
          <li key={i} className="flex items-center gap-2 text-xs text-zinc-500">
            <span className={s.done ? 'text-emerald-400' : 'text-zinc-600'}>{s.done ? '✓' : '⏳'}</span>
            {s.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ScoreGauge({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#f43f5e';
  const r = 42;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;
  return (
    <div className="flex items-center gap-3">
      <svg width="100" height="100" viewBox="0 0 100 100" className="shrink-0">
        <circle cx="50" cy="50" r={r} fill="none" stroke="#27272a" strokeWidth="8" />
        <circle
          cx="50"
          cy="50"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform="rotate(-90 50 50)"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      <div>
        <div className="text-2xl font-bold text-white">{pct}<span className="text-sm text-zinc-500">/100</span></div>
        <div className="text-xs text-zinc-500">Differentiation</div>
      </div>
    </div>
  );
}

function ReportView({ report, ideaText }: { report: IdeaValidationReport; ideaText: string }) {
  const verdict = VERDICT_META[report.verdict] ?? VERDICT_META.moderate;
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-zinc-500">Verdict</div>
            <div className="mt-1 text-lg font-semibold text-white">
              {verdict.emoji} {verdict.label}
            </div>
          </div>
          <ScoreGauge score={report.differentiationScore} />
        </div>
        <p className="mt-4 text-sm leading-relaxed text-zinc-300">{report.summary}</p>
        {report.differentiation && (
          <div className="mt-4 rounded-lg border border-violet-500/20 bg-violet-950/15 p-3">
            <div className="text-xs font-medium text-violet-300">Your wedge</div>
            <p className="mt-1 text-sm text-zinc-300">{report.differentiation}</p>
          </div>
        )}
        <details className="mt-3 text-xs text-zinc-600">
          <summary className="cursor-pointer hover:text-zinc-400">Original idea</summary>
          <p className="mt-2 whitespace-pre-wrap rounded bg-black/30 p-2">{ideaText}</p>
        </details>
      </div>

      {report.competitors.length > 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
          <h3 className="text-sm font-semibold text-white">
            Competitors ({report.competitors.length})
          </h3>
          <div className="mt-3 space-y-3">
            {report.competitors.map((c, i) => (
              <div key={i} className="rounded-lg border border-zinc-800/60 bg-black/30 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-violet-300 hover:underline"
                    >
                      {c.name}
                    </a>
                    <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase text-zinc-400">
                      {c.type}
                    </span>
                    {typeof c.stars === 'number' && (
                      <span className="ml-2 text-xs text-amber-400">★ {c.stars.toLocaleString()}</span>
                    )}
                  </div>
                </div>
                {c.description && <p className="mt-1 text-xs text-zinc-400">{c.description}</p>}
                {c.differentiation && (
                  <p className="mt-1 text-xs text-zinc-500">
                    <span className="text-zinc-600">vs yours:</span> {c.differentiation}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {report.openSourceReuse.length > 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
          <h3 className="text-sm font-semibold text-white">
            Reusable open source ({report.openSourceReuse.length})
          </h3>
          <div className="mt-3 space-y-3">
            {report.openSourceReuse.map((r, i) => (
              <div key={i} className="rounded-lg border border-emerald-500/20 bg-emerald-950/10 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-emerald-300">{r.repo}</span>
                  <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-[10px] text-emerald-300">
                    {r.license}
                  </span>
                  {r.savedTimeEstimate && (
                    <span className="text-xs text-zinc-500">~{r.savedTimeEstimate} saved</span>
                  )}
                </div>
                {r.whatToReuse && <p className="mt-1 text-xs text-zinc-400">{r.whatToReuse}</p>}
                {r.modulePath && (
                  <p className="mt-1 font-mono text-[11px] text-zinc-600">{r.modulePath}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryList({
  history,
  accessToken,
  onSelect,
}: {
  history: IdeaCheck[];
  accessToken: string;
  onSelect: (row: IdeaCheck) => void;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
      <h3 className="text-sm font-semibold text-white">Recent checks</h3>
      <ul className="mt-3 space-y-2">
        {history.slice(0, 8).map((row) => (
          <li key={row.id}>
            <button
              onClick={async () => {
                onSelect(row);
                if (!row.viewed) {
                  void fetch(apiUrl(`/api/idea-validator/check/${row.id}`), {
                    method: 'PATCH',
                    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ viewed: true }),
                  });
                }
              }}
              className="flex w-full items-center justify-between gap-3 rounded-lg border border-zinc-800/60 bg-black/20 p-2 text-left hover:border-zinc-700"
            >
              <span className="min-w-0 flex-1 truncate text-xs text-zinc-400">{row.ideaText}</span>
              <span className="shrink-0 text-[10px] uppercase text-zinc-600">{row.status}</span>
              {!row.viewed && row.status === 'COMPLETED' && (
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400" />
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

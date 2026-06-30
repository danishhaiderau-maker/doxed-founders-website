'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatUsd } from '@dcf/utils';
import {
  fetchAnalyzerGenome,
  fetchAnalyzerSessionSummary,
  type AnalyzerGenome,
  type AnalyzerSessionSummary,
} from '@/lib/api';

const POLL_MS = 60_000;

function fmtPct(n: number | undefined, digits = 1): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return `${n.toFixed(digits)}%`;
}

function fmtHours(n: number | undefined): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return '—';
  if (n < 24) return `${n.toFixed(1)}h`;
  return `${(n / 24).toFixed(1)}d`;
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5 text-center">
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${accent ?? 'text-white'}`}>{value}</p>
    </div>
  );
}

export function AgentAnalyzerPanel({ slug }: { slug: string }) {
  const [summary, setSummary] = useState<AnalyzerSessionSummary | null>(null);
  const [genome, setGenome] = useState<AnalyzerGenome | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchAnalyzerSessionSummary(slug),
      fetchAnalyzerGenome(slug),
    ]);
    if (results[0].status === 'fulfilled') setSummary(results[0].value);
    else setSummary((prev) => prev ?? { ok: false, error: 'summary fetch failed' });
    if (results[1].status === 'fulfilled') setGenome(results[1].value);
    else setGenome((prev) => prev ?? { ok: false, error: 'genome fetch failed' });
    setLoading(false);
  }, [slug]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const sessionStart = summary?.session_start ?? null;
  const pnl = summary?.total_pnl_usd ?? 0;
  const pnlAccent = pnl >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/25 to-zinc-950/60 p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
              Conservative BTC Agent · full session
            </p>
            <h2 className="mt-1 text-lg font-bold text-white">From last Fresh Collection wipeout</h2>
            <p className="mt-1 max-w-2xl text-xs text-zinc-500">
              Long-session analytics from the research analyzer (:9001). Balance, P&amp;L, trade count,
              and win rate cover the entire session since the last wipe — not just the rolling snapshot.
            </p>
          </div>
          <div className="text-right text-[10px] text-zinc-600">
            <p>source: {summary?.source ?? 'analyzer :9001'}</p>
            <p>generated: {summary?.generated_at ?? '—'}</p>
          </div>
        </div>

        {loading ? (
          <p className="mt-4 text-sm text-zinc-500">Loading analyzer session…</p>
        ) : summary?.ok ? (
          <>
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Metric label="Session start" value={sessionStart ? new Date(sessionStart).toLocaleString() : '—'} />
              <Metric label="Session length" value={fmtHours(summary?.session_hours)} />
              <Metric label="Starting balance" value={formatUsd(summary?.starting_balance ?? 0, 0)} />
              <Metric label="Current balance" value={formatUsd(summary?.current_balance ?? 0, 0)} />
              <Metric label="Total P&L" value={`${pnl >= 0 ? '+' : ''}${formatUsd(pnl, 2)}`} accent={pnlAccent} />
              <Metric label="Total P&L %" value={fmtPct(summary?.total_pnl_pct)} accent={pnlAccent} />
              <Metric label="Trades" value={String(summary?.trade_count ?? 0)} />
              <Metric label="Win rate" value={fmtPct(summary?.win_rate)} accent="text-emerald-300" />
              <Metric label="Approves" value={String(summary?.approve_count ?? 0)} />
              <Metric label="Executed" value={String(summary?.executed_count ?? 0)} />
              <Metric label="Coverage" value={summary?.coverage_status ?? '—'} />
              <Metric label="Data scope" value={summary?.data_scope ?? '—'} />
            </div>
            {summary?.executive_text && (
              <pre className="mt-4 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-zinc-800/80 bg-black/40 p-4 text-[11px] leading-relaxed text-zinc-300">
                {summary.executive_text}
              </pre>
            )}
          </>
        ) : (
          <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-950/15 px-4 py-3 text-xs text-amber-100/90">
            Analyzer session unavailable
            {summary?.error ? ` — ${summary.error}` : ''}
            <p className="mt-1 text-amber-200/70">
              The research analyzer runs on the home PC at :9001. This block reads it through the bot&apos;s
              public tunnel (<code>/api/analyzer/summary</code>). If the bot has not been restarted since the
              proxy route was added, restart the showcase bot to enable this view.
            </p>
          </div>
        )}
      </section>

      <GenomeSection genome={genome} loading={loading} />
    </div>
  );
}

function GenomeSection({ genome, loading }: { genome: AnalyzerGenome | null; loading: boolean }) {
  const discoveriesCount = genome?.discoveries_count ?? 0;
  const libraryCount = genome?.library_count ?? 0;
  const stats = genome?.genome_stats as Record<string, unknown> | null | undefined;

  return (
    <section className="rounded-2xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/15 to-zinc-950/60 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-300">
            Decision genome
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Trade → DNA → Outcome memory</h2>
          <p className="mt-1 max-w-2xl text-xs text-zinc-500">
            Genome is independent of trade source (Bitfinex / sim / replay / CSV). It consumes every closed
            trade&apos;s DNA and records the realized outcome to build a memory of what works.
          </p>
        </div>
        <div className="text-right text-[10px] text-zinc-600">
          <p>mode: {genome?.analyzer_mode ?? '—'}</p>
          <p>arch: {genome?.architecture_frozen ?? '—'}</p>
        </div>
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-zinc-500">Loading genome…</p>
      ) : genome?.ok ? (
        <>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Library entries" value={String(libraryCount)} />
            <Metric label="Discoveries" value={String(discoveriesCount)} />
            <Metric
              label="Trades documented"
              value={String((stats?.trades_documented as number) ?? (stats?.trades_seen as number) ?? 0)}
            />
            <Metric
              label="Outcomes recorded"
              value={String((stats?.outcomes_recorded as number) ?? 0)}
            />
          </div>
          {stats && (
            <pre className="mt-4 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-zinc-800/80 bg-black/40 p-3 text-[10px] leading-relaxed text-zinc-400">
              {JSON.stringify(stats, null, 2)}
            </pre>
          )}
        </>
      ) : (
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-950/15 px-4 py-3 text-xs text-amber-100/90">
          Genome unavailable
          {genome?.error ? ` — ${genome.error}` : ''}
          <p className="mt-1 text-amber-200/70">
            The analyzer exposes <code>/api/genome</code> at :9001. This block reads it through the bot&apos;s
            public tunnel proxy. Restart the showcase bot if the proxy route is not yet live.
          </p>
        </div>
      )}
    </section>
  );
}

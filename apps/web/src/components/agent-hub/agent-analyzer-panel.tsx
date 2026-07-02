'use client';

import { useCallback, useEffect, useState } from 'react';
import { formatUsd } from '@dcf/utils';
import {
  fetchAnalyzerSessionSummary,
  type AnalyzerSessionSummary,
} from '@/lib/api';
import { SpreadGateControl } from '@/components/agent-hub/spread-gate-control';

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

// Analyzer session_start arrives as "YYYY-MM-DD HH:MM:SS TZ" (e.g. "2026-07-01 10:25:10 AEST"),
// which `new Date()` cannot parse. Fall back to a manual parse that preserves the TZ label.
function formatSessionStart(raw: string | null | undefined): string {
  if (!raw) return '—';
  const direct = new Date(raw);
  if (!Number.isNaN(direct.getTime())) return direct.toLocaleString();
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\s+(\S+))?$/);
  if (m) {
    const [, y, mo, d, h, mi, s, tz] = m;
    const dt = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
    if (!Number.isNaN(dt.getTime())) {
      return tz ? `${y}-${mo}-${d} ${h}:${mi} ${tz}` : dt.toLocaleString();
    }
  }
  return raw;
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5 text-center">
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${accent ?? 'text-white'}`}>{value}</p>
    </div>
  );
}

export function AgentAnalyzerPanel({
  slug,
  summary: summaryProp,
}: {
  slug: string;
  summary?: AnalyzerSessionSummary | null;
}) {
  const [summary, setSummary] = useState<AnalyzerSessionSummary | null>(summaryProp ?? null);
  const [loading, setLoading] = useState(summaryProp === undefined);

  const load = useCallback(async () => {
    const results = await Promise.allSettled([fetchAnalyzerSessionSummary(slug)]);
    if (results[0].status === 'fulfilled') setSummary(results[0].value);
    else setSummary((prev) => prev ?? { ok: false, error: 'summary fetch failed' });
    setLoading(false);
  }, [slug]);

  useEffect(() => {
    if (summaryProp !== undefined) {
      setSummary(summaryProp);
      setLoading(false);
    }
  }, [summaryProp]);

  useEffect(() => {
    // Parent owns the fetch when it passes a summary prop (even null while loading).
    if (summaryProp !== undefined) return;
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load, summaryProp]);

  const sessionStart = summary?.session_start ?? null;
  const sessionStartLabel = formatSessionStart(sessionStart);
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
              Cumulative session analytics — balance, P&amp;L, trade count, and win rate cover the entire
              session since the last wipe, not just the rolling snapshot. Read from the showcase bot&apos;s
              full-session state (cached ~60s).
            </p>
          </div>
          <div className="text-right text-[10px] text-zinc-600">
            <p>source: {summary?.source ?? 'bot /api/state'}</p>
            <p>generated: {summary?.generated_at ?? '—'}</p>
          </div>
        </div>

        {loading ? (
          <p className="mt-4 text-sm text-zinc-500">Loading analyzer session…</p>
        ) : summary?.ok ? (
          <>
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Metric label="Session start" value={sessionStartLabel} />
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
              The showcase bot (Fly + Cloudflare) is unreachable. Cumulative session metrics will reappear
              once either endpoint responds 200.
            </p>
          </div>
        )}
      </section>

      {slug === 'conservative-btc' && <SpreadGateControl />}
    </div>
  );
}

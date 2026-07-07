'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';

type ProviderBreakdown = {
  provider: string;
  requests: number;
  promptTokens: number;
  completionTokens: number;
  ddollarSpent: number;
};

type UsageSummary = {
  totals: {
    requests: number;
    promptTokens: number;
    completionTokens: number;
    ddollarSpent: number;
    estimatedCursorProCost: number;
  };
  providers: ProviderBreakdown[];
  daily: Array<{
    day: string;
    requests: number;
    promptTokens: number;
    completionTokens: number;
    ddollarSpent: number;
  }>;
};

type Props = {
  accessToken: string;
};

const fmtInt = (n: number) => n.toLocaleString();
const fmtUsd = (n: number) =>
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
const fmtDd = (n: number) => `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} DD`;

function StatCard({
  label,
  value,
  hint,
  accent = 'zinc',
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: 'zinc' | 'emerald' | 'violet' | 'amber';
}) {
  const accentText =
    accent === 'emerald'
      ? 'text-emerald-300'
      : accent === 'violet'
        ? 'text-violet-300'
        : accent === 'amber'
          ? 'text-amber-300'
          : 'text-white';
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">{label}</p>
      <p className={`mt-2 text-2xl font-bold ${accentText}`}>{value}</p>
      {hint ? <p className="mt-1 text-[11px] text-zinc-500">{hint}</p> : null}
    </div>
  );
}

function CopyableField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <label className="block text-sm">
      <span className="text-zinc-400">{label}</span>
      <div className="mt-1 flex gap-2">
        <input
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 font-mono text-xs text-zinc-200"
        />
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              // ignore clipboard failures
            }
          }}
          className="rounded-lg border border-zinc-600 px-3 py-2 text-xs text-zinc-200 hover:border-zinc-400 hover:text-white"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </label>
  );
}

export function AiProxyDashboard({ accessToken }: Props) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(apiUrl('/v1/usage-for-me?days=30'), {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg =
          (body as { error?: { message?: string } | string })?.error &&
          (typeof (body as { error: { message?: string } }).error === 'object'
            ? (body as { error: { message?: string } }).error.message
            : String((body as { error: string }).error));
        throw new Error(msg ?? `Request failed (${res.status})`);
      }
      const data = (await res.json()) as UsageSummary;
      setSummary(data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load AI usage summary');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return <p className="text-sm text-zinc-500">Loading AI usage…</p>;
  }

  if (err) {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-950/15 p-4 text-sm text-red-100">
        <p className="font-semibold">Could not load usage</p>
        <p className="mt-1 text-xs text-red-200/80">{err}</p>
        <button
          type="button"
          onClick={load}
          className="mt-3 rounded-lg border border-red-400/40 px-3 py-1.5 text-xs text-red-100 hover:bg-red-500/10"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!summary) return null;

  const proxyBaseUrl =
    typeof window !== 'undefined'
      ? `${window.location.origin}/api/v1`
      : '/api/v1';

  return (
    <div className="space-y-8">
      {/* Top stats */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-zinc-400">
          Last 30 days
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <StatCard label="Requests" value={fmtInt(summary.totals.requests)} />
          <StatCard
            label="Prompt tokens"
            value={fmtInt(summary.totals.promptTokens)}
            accent="violet"
          />
          <StatCard
            label="Completion tokens"
            value={fmtInt(summary.totals.completionTokens)}
            accent="violet"
          />
          <StatCard
            label="DDollar spent"
            value={fmtDd(summary.totals.ddollarSpent)}
            accent="amber"
          />
          <StatCard
            label="Cursor Pro saved"
            value={fmtUsd(summary.totals.estimatedCursorProCost)}
            hint="vs $20/mo retail"
            accent="emerald"
          />
        </div>
      </section>

      {/* Per-provider breakdown */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-zinc-400">
          By provider
        </h2>
        {summary.providers.length === 0 ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-6 text-sm text-zinc-500">
            No requests yet. Connect Cursor or another OpenAI-compatible client below to start
            routing through the proxy.
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-900/60 text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                <tr>
                  <th className="px-4 py-3 font-semibold">Provider</th>
                  <th className="px-4 py-3 text-right font-semibold">Requests</th>
                  <th className="px-4 py-3 text-right font-semibold">Prompt</th>
                  <th className="px-4 py-3 text-right font-semibold">Completion</th>
                  <th className="px-4 py-3 text-right font-semibold">DDollar</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60 bg-zinc-950/40">
                {summary.providers.map((row) => (
                  <tr key={row.provider} className="text-zinc-200">
                    <td className="px-4 py-3 font-mono text-xs text-zinc-100">{row.provider}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtInt(row.requests)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-zinc-400">
                      {fmtInt(row.promptTokens)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-zinc-400">
                      {fmtInt(row.completionTokens)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-amber-200">
                      {fmtDd(row.ddollarSpent)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Connect Cursor */}
      <section className="rounded-xl border border-violet-500/30 bg-violet-950/10 p-5">
        <h2 className="text-sm font-semibold text-violet-100">Connect Cursor</h2>
        <p className="mt-1 text-xs text-zinc-400">
          Point Cursor (or any OpenAI-compatible client) at the Founder OS proxy. Your Founder
          Node credentials act as the API key.
        </p>
        <div className="mt-4 space-y-3">
          <CopyableField label="Base URL" value={proxyBaseUrl} />
          <CopyableField
            label="Authorization header"
            value="FounderNode <nodeId>:<nodeToken>"
          />
        </div>
        <p className="mt-4 text-[11px] text-zinc-500">
          Don&rsquo;t have a Founder Node token? Pair a node from{' '}
          <a
            href="/settings/builder?tab=founder-node"
            className="text-violet-300 underline hover:text-violet-200"
          >
            Founder Node settings
          </a>
          .
        </p>
      </section>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500 hover:text-white"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}

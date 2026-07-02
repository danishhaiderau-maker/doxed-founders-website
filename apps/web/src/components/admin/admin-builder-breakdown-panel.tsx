'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAdminBuilderBreakdown,
  flagParasite,
  type BuilderTierBreakdown,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function AdminBuilderBreakdownPanel({ accessToken }: Props) {
  const [data, setData] = useState<BuilderTierBreakdown | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await fetchAdminBuilderBreakdown(accessToken);
      setData(d);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load builder breakdown');
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleFlag(userId: string, email: string) {
    setBusy(userId);
    setErr(null);
    setMsg(null);
    try {
      await flagParasite(accessToken, userId);
      setMsg(`Flagged ${email} as PARASITE (score reset to 0).`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Flag failed');
    } finally {
      setBusy(null);
    }
  }

  if (!data && !err) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 px-4 py-3 text-sm text-amber-100/80">
        Loading builder vs parasite breakdown…
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-950/15 px-4 py-3 text-sm text-red-300">
        {err}
      </div>
    );
  }

  const parasiteSpend = data.spendTodayByTier.find((r) => r.tier === 'PARASITE')?.tokens ?? 0;
  const builderSpend = data.spendTodayByTier.find((r) => r.tier === 'VERIFIED_BUILDER')?.tokens ?? 0;
  const parasiteCount = data.accountCountsByTier.find((r) => r.tier === 'PARASITE')?.count ?? 0;
  const builderCount = data.accountCountsByTier.find((r) => r.tier === 'VERIFIED_BUILDER')?.count ?? 0;
  const poolPct = (data.poolRemainingFraction * 100).toFixed(1);

  return (
    <div className="rounded-xl border border-violet-500/40 bg-gradient-to-br from-violet-950/30 to-zinc-950/50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-violet-400">Admin only</p>
          <h3 className="mt-1 text-lg font-semibold text-white">Builder vs parasite breakdown</h3>
          <p className="mt-1 max-w-xl text-xs text-zinc-400">
            Two-tier pool protection. Verified builders (xVerified + GitHub/Cursor + recent commit) get
            a higher daily token cap and reserved quota when the pool runs low. Parasites are cut off
            below the preservation threshold.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy != null}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:text-white disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Pool remaining" value={`${fmtTokens(data.poolRemaining)} / ${fmtTokens(data.poolCap)}`} sub={`${poolPct}%`} />
        <Stat label="Today — parasite spend" value={fmtTokens(parasiteSpend)} sub="tokens" tone="amber" />
        <Stat label="Today — builder spend" value={fmtTokens(builderSpend)} sub="tokens" tone="emerald" />
        <Stat
          label="Accounts (P / V)"
          value={`${parasiteCount} / ${builderCount}`}
          sub="parasite / builder"
        />
      </div>

      <div className="mt-5 rounded-lg border border-zinc-800 bg-black/30 p-3">
        <p className="text-xs font-semibold text-zinc-300">Top parasite-tier accounts by 24h token spend</p>
        {data.topParasitesBy24h.length === 0 ? (
          <p className="mt-2 text-[11px] text-zinc-500">
            No parasite-tier platform-token usage in the last 24h (or builderTier column not yet
            migrated — run `npx prisma migrate deploy` on Railway after this merge).
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="py-1 pr-3 font-medium">Email</th>
                  <th className="py-1 pr-3 font-medium">Handle</th>
                  <th className="py-1 pr-3 font-medium">Tokens</th>
                  <th className="py-1 pr-3 font-medium">Calls</th>
                  <th className="py-1 pr-3 font-medium">Score</th>
                  <th className="py-1 pr-3 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="text-zinc-300">
                {data.topParasitesBy24h.map((u) => (
                  <tr key={u.userId} className="border-t border-zinc-800/60">
                    <td className="py-1.5 pr-3 font-mono text-[11px]">{u.email}</td>
                    <td className="py-1.5 pr-3 text-zinc-400">{u.twitterHandle ?? '—'}</td>
                    <td className="py-1.5 pr-3">{fmtTokens(u.tokens)}</td>
                    <td className="py-1.5 pr-3">{u.calls}</td>
                    <td className="py-1.5 pr-3 text-zinc-400">{u.builderScore}</td>
                    <td className="py-1.5 pr-3">
                      <button
                        type="button"
                        disabled={busy != null}
                        onClick={() => void handleFlag(u.userId, u.email)}
                        className="rounded border border-red-500/40 bg-red-950/30 px-2 py-0.5 text-[10px] text-red-200 hover:bg-red-950/50 disabled:opacity-50"
                      >
                        {busy === u.userId ? 'Flagging…' : 'Flag'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="mt-4 rounded-lg border border-zinc-800 bg-black/20 p-3">
        <p className="text-xs font-semibold text-zinc-300">Active env vars (read-only)</p>
        <div className="mt-2 grid gap-2 text-[11px] text-zinc-400 sm:grid-cols-2 lg:grid-cols-3">
          <EnvRow name="PARASITE_DAILY_TOKEN_CAP" value={fmtTokens(data.env.PARASITE_DAILY_TOKEN_CAP)} />
          <EnvRow name="BUILDER_DAILY_TOKEN_CAP" value={fmtTokens(data.env.BUILDER_DAILY_TOKEN_CAP)} />
          <EnvRow
            name="PROMO_POOL_PRESERVATION_PCT"
            value={`${(data.env.PROMO_POOL_PRESERVATION_PCT * 100).toFixed(0)}%`}
          />
          <EnvRow name="BUILDER_SCORE_THRESHOLD" value={String(data.env.BUILDER_SCORE_THRESHOLD)} />
          <EnvRow
            name="BUILDER_SCORE_REFRESH_TTL_MS"
            value={`${(data.env.BUILDER_SCORE_REFRESH_TTL_MS / 60000).toFixed(0)}min`}
          />
        </div>
      </div>

      {msg && <p className="mt-2 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone = 'zinc',
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'zinc' | 'amber' | 'emerald';
}) {
  const toneClass =
    tone === 'amber'
      ? 'text-amber-300'
      : tone === 'emerald'
        ? 'text-emerald-300'
        : 'text-white';
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/20 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${toneClass}`}>{value}</p>
      {sub && <p className="text-[10px] text-zinc-500">{sub}</p>}
    </div>
  );
}

function EnvRow({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded border border-zinc-800/60 bg-black/30 px-2 py-1">
      <span className="font-mono text-zinc-500">{name}</span>
      <span className="font-mono text-zinc-200">{value}</span>
    </div>
  );
}

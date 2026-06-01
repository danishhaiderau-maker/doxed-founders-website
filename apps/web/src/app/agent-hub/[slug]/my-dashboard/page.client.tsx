'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { formatPercent } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { fetchMyAgentDashboard, type PrivateAgentDashboard } from '@/lib/api';

export default function AgentMyDashboardClient({ slug }: { slug: string }) {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;

  const [data, setData] = useState<PrivateAgentDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const dash = await fetchMyAgentDashboard(slug, token);
      setData(dash);
      setError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load dashboard';
      if (msg.includes('hire')) {
        router.replace(`/agent-hub/${slug}/hire`);
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [slug, token, router]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace(`/login?callbackUrl=/agent-hub/${slug}/my-dashboard`);
      return;
    }
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [status, router, slug, load]);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div>
            <SiteBrand className="text-sm" />
            <p className="mt-1 text-xs text-emerald-400/90">Private instance — isolated from public showcase</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-10">
        {loading ? (
          <p className="text-zinc-500">Loading your dashboard…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        ) : data ? (
          <>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold">{data.agent.name}</h1>
                <p className="mt-1 text-sm text-zinc-400">My Agent · {data.agent.assetSymbol}</p>
              </div>
              <Link
                href={`/agent-hub/${slug}`}
                className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:text-white"
              >
                View public showcase →
              </Link>
            </div>

            <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Status" value={data.instance.status} />
              <Stat label="Exchange" value={data.instance.exchangeLabel} />
              <Stat label="AI" value={data.instance.aiLabel} />
              <Stat
                label="PnL"
                value={formatPercent(data.runtime.pnlPct)}
                highlight={data.runtime.pnlPct >= 0 ? 'up' : 'down'}
              />
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              <Stat label="Open positions" value={String(data.runtime.openPositions)} />
              <Stat
                label="Exchange API"
                value={data.exchange.connected ? 'Connected' : 'Disconnected'}
                highlight={data.exchange.connected ? 'up' : 'down'}
              />
            </div>

            {data.instance.lastError && (
              <p className="mt-4 rounded-lg border border-amber-500/30 bg-amber-950/20 px-4 py-3 text-sm text-amber-200">
                {data.instance.lastError}
              </p>
            )}

            <div className="mt-8 rounded-xl border border-zinc-800 bg-zinc-950/40 p-5 text-sm text-zinc-400">
              <p className="font-medium text-zinc-200">Isolation guarantee</p>
              <p className="mt-2">{data.runtime.message}</p>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-xs">
                <li>No shared orders, positions, balances, or PnL with other users</li>
                <li>Admin showcase uses separate API keys — never yours</li>
                <li>Platform provides AI — no API key required from you</li>
              </ul>
              <p className="mt-3 text-xs text-zinc-500">
                Hired {new Date(data.instance.hiredAt).toLocaleDateString()}
                {data.instance.activatedAt &&
                  ` · Activated ${new Date(data.instance.activatedAt).toLocaleDateString()}`}
              </p>
            </div>
          </>
        ) : null}
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: 'up' | 'down';
}) {
  const color =
    highlight === 'up' ? 'text-emerald-400' : highlight === 'down' ? 'text-red-400' : 'text-white';
  return (
    <div className="rounded-xl border border-zinc-800 bg-black/20 px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold capitalize ${color}`}>{value}</p>
    </div>
  );
}

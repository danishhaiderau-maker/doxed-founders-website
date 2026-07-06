'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { fetchAccountOverview, fetchObservatoryOverview, type ObservatoryOverview } from '@/lib/api';

const STATUS_DOT: Record<string, string> = {
  green: 'bg-emerald-400',
  yellow: 'bg-amber-400',
  red: 'bg-red-500',
  unknown: 'bg-zinc-500',
};

export default function AdminObservatoryPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const sessionAdmin = session?.user?.role === 'ADMIN';
  const [accountAdmin, setAccountAdmin] = useState(false);
  const isAdmin = sessionAdmin || accountAdmin;

  const [overview, setOverview] = useState<ObservatoryOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setOverview(await fetchObservatoryOverview(token));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load observatory');
    }
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/observatory');
      return;
    }
    if (token && !sessionAdmin) {
      fetchAccountOverview(token)
        .then((ov) => setAccountAdmin(ov.isAdmin))
        .catch(() => setAccountAdmin(false));
    }
  }, [status, token, sessionAdmin, router]);

  useEffect(() => {
    if (status === 'loading' || !isAdmin) return;
    load();
  }, [status, isAdmin, load]);

  if (!isAdmin && status !== 'loading') return null;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <SiteNav />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-widest text-violet-400">Founder OS</p>
            <h1 className="text-2xl font-bold">Observatory</h1>
            <p className="text-sm text-zinc-400">Internal control room — subsystem health, version, last smoke test.</p>
          </div>
          <Link href="/admin/demo" className="text-sm text-emerald-400 hover:underline">
            Demo Mode →
          </Link>
        </div>

        {error && <p className="mb-4 text-sm text-red-400">{error}</p>}

        {overview && !overview.enabled && (
          <p className="rounded-xl border border-amber-500/30 bg-amber-950/20 p-4 text-sm text-amber-100">
            {overview.message ?? 'Set OBSERVATORY_ENABLED=true to activate the control room.'}
          </p>
        )}

        {overview?.enabled && (
          <>
            <div className="mb-6 grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                <p className="text-xs text-zinc-500">Version</p>
                <p className="font-mono text-lg">{overview.version}</p>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                <p className="text-xs text-zinc-500">Probe latency</p>
                <p className="font-mono text-lg">{overview.latencyMs}ms</p>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                <p className="text-xs text-zinc-500">Last smoke</p>
                <p className="text-sm">
                  {overview.lastSmoke
                    ? `${overview.lastSmoke.passed}/${overview.lastSmoke.total} passed · ${new Date(overview.lastSmoke.ranAt).toLocaleString()}`
                    : 'Not run yet — use /admin/demo smoke'}
                </p>
              </div>
            </div>

            <div className="overflow-x-auto rounded-xl border border-zinc-800">
              <table className="min-w-full text-left text-sm">
                <thead className="border-b border-zinc-800 bg-zinc-900/60 text-xs uppercase text-zinc-500">
                  <tr>
                    <th className="px-4 py-3">Subsystem</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Coverage</th>
                    <th className="px-4 py-3">Latency</th>
                    <th className="px-4 py-3">Last test</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.subsystems.map((row) => (
                    <tr key={row.id} className="border-b border-zinc-800/80">
                      <td className="px-4 py-3">
                        <p className="font-medium text-white">{row.label}</p>
                        <p className="text-xs text-zinc-500">{row.description}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center gap-2 capitalize">
                          <span className={`h-2 w-2 rounded-full ${STATUS_DOT[row.status] ?? STATUS_DOT.unknown}`} />
                          {row.status}
                        </span>
                        {row.lastError && <p className="text-xs text-red-400">{row.lastError}</p>}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">{row.coverage ?? '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs">{row.latencyMs != null ? `${row.latencyMs}ms` : '—'}</td>
                      <td className="px-4 py-3 text-xs">
                        {row.lastTest ? (
                          <span className={row.lastTest.passed ? 'text-emerald-400' : 'text-red-400'}>
                            {row.lastTest.name} · {row.lastTest.passed ? 'pass' : 'fail'}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { fetchAccountOverview, fetchDemoStatus, resetDemoData, runDemoSmokeChecks, seedDemoEcosystem, type DemoSmokeReport, type DemoStatus } from '@/lib/api';

export default function AdminDemoPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const sessionAdmin = session?.user?.role === 'ADMIN';
  const [accountAdmin, setAccountAdmin] = useState(false);
  const isAdmin = sessionAdmin || accountAdmin;

  const [demoStatus, setDemoStatus] = useState<DemoStatus | null>(null);
  const [smoke, setSmoke] = useState<DemoSmokeReport | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const st = await fetchDemoStatus(token);
      setDemoStatus(st);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load demo status');
    }
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/demo');
      return;
    }
    if (token && !sessionAdmin) {
      fetchAccountOverview(token)
        .then((ov) => setAccountAdmin(ov.isAdmin))
        .catch(() => setAccountAdmin(false));
    }
  }, [status, token, sessionAdmin, router]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') return;
    if (!isAdmin) {
      router.replace('/');
      return;
    }
    load();
  }, [status, isAdmin, router, load]);

  async function handleSeed() {
    if (!token) return;
    setBusy('seed');
    setMsg(null);
    setError(null);
    try {
      const res = await seedDemoEcosystem(token);
      setMsg(res.message ?? 'Demo ecosystem seeded.');
      setDemoStatus(res.status ?? null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Seed failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleReset() {
    if (!token) return;
    if (!window.confirm('Remove all demo-tagged users and projects? Real data is never touched.')) return;
    setBusy('reset');
    setMsg(null);
    setError(null);
    try {
      const res = await resetDemoData(token);
      setMsg(res.message ?? 'Demo data reset.');
      setSmoke(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleSmoke() {
    if (!token) return;
    setBusy('smoke');
    setMsg(null);
    setError(null);
    try {
      const report = await runDemoSmokeChecks(token);
      setSmoke(report);
      setMsg(`Smoke: ${report.passed}/${report.total} passed`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Smoke checks failed');
    } finally {
      setBusy(null);
    }
  }

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#050508] text-zinc-400">
        Loading demo control…
      </div>
    );
  }

  if (!isAdmin) return null;

  const counts = demoStatus?.counts;

  return (
    <div className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div>
            <Link href="/admin/control" className="text-xs text-zinc-500 hover:text-white">
              ← Admin Control
            </Link>
            <h1 className="text-xl font-bold">Demo Mode</h1>
            <p className="text-sm text-zinc-500">Seed synthetic Founder OS ecosystem for E2E testing</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        {!demoStatus?.enabled && (
          <div className="rounded-xl border border-amber-500/40 bg-amber-950/20 px-4 py-3 text-sm text-amber-100">
            <p className="font-semibold">Demo mode disabled on API</p>
            <p className="mt-1 text-xs text-amber-200/90">
              Set <code className="text-amber-50">DEMO_MODE_ENABLED=true</code> on Railway (API service) and redeploy.
              Seed/reset/smoke endpoints fail closed until then.
            </p>
          </div>
        )}

        {msg && (
          <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
            {msg}
          </p>
        )}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="text-lg font-semibold">Actions</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Scale: <span className="text-zinc-300">{demoStatus?.scale ?? 'medium'}</span> via{' '}
            <code className="text-zinc-400">DEMO_SEED_SCALE</code> on Railway.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy != null || !demoStatus?.enabled}
              onClick={() => void handleSeed()}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500 disabled:opacity-50"
            >
              {busy === 'seed' ? 'Seeding…' : 'Generate Demo Ecosystem'}
            </button>
            <button
              type="button"
              disabled={busy != null || !demoStatus?.enabled}
              onClick={() => void handleReset()}
              className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm text-red-200 hover:bg-red-950/50 disabled:opacity-50"
            >
              {busy === 'reset' ? 'Resetting…' : 'Reset Demo Data'}
            </button>
            <button
              type="button"
              disabled={busy != null || !demoStatus?.enabled}
              onClick={() => void handleSmoke()}
              className="rounded-lg border border-emerald-500/40 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200 hover:text-white disabled:opacity-50"
            >
              {busy === 'smoke' ? 'Running…' : 'Run Smoke Checks'}
            </button>
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Demo users" value={counts ? String(counts.users) : '—'} />
          <StatCard label="Demo projects" value={counts ? String(counts.projects) : '—'} />
          <StatCard label="Active raises" value={counts ? String(counts.activeRaises) : '—'} />
          <StatCard
            label="Paper raise total"
            value={counts ? `$${counts.totalPaperRaiseUsd.toLocaleString()}` : '—'}
          />
          <StatCard label="Founders" value={counts ? String(counts.founders) : '—'} />
          <StatCard label="Allocations" value={counts ? String(counts.raiseAllocations) : '—'} />
          <StatCard
            label="Lifetime contribution (ledger+)"
            value={counts ? counts.lifetimeContributionPoints.toLocaleString() : '—'}
          />
          <StatCard label="Seeded" value={demoStatus?.seeded ? 'Yes' : 'No'} />
        </section>

        {demoStatus?.samples && (
          <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6 text-sm text-zinc-400">
            <h2 className="font-semibold text-white">Sample routes</h2>
            <ul className="mt-3 space-y-2">
              <li>
                Project:{' '}
                <Link href={`/projects/${demoStatus.samples.projectSlug}`} className="text-violet-400 hover:underline">
                  /projects/{demoStatus.samples.projectSlug}
                </Link>
              </li>
              <li>
                Raise Room:{' '}
                <Link href="/raise-room" className="text-violet-400 hover:underline">
                  /raise-room
                </Link>
              </li>
              <li>Demo user email: {demoStatus.samples.userEmail}</li>
            </ul>
          </section>
        )}

        {smoke && (
          <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
            <h2 className="text-lg font-semibold">
              Smoke report — {smoke.passed}/{smoke.total} passed
            </h2>
            <p className="mt-1 text-xs text-zinc-500">Ran {new Date(smoke.ranAt).toLocaleString()}</p>
            <ul className="mt-4 space-y-2">
              {smoke.checks.map((check) => (
                <li
                  key={check.name}
                  className={`rounded-lg border px-3 py-2 text-sm ${
                    check.passed
                      ? 'border-emerald-500/30 bg-emerald-950/10 text-emerald-100'
                      : 'border-red-500/30 bg-red-950/10 text-red-200'
                  }`}
                >
                  <span className="font-mono text-xs">{check.name}</span>
                  <span className="ml-2">{check.detail}</span>
                  <span className="ml-2 text-xs text-zinc-500">({check.durationMs}ms)</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/20 px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-white">{value}</p>
    </div>
  );
}

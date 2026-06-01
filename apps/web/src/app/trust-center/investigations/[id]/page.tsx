'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { contributorLevelLabel, VALIDATION_LABELS } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import {
  fetchTrustInvestigationDetail,
  resolveTrustInvestigation,
  type TrustInvestigation,
} from '@/lib/api';

export default function InvestigationDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const [investigation, setInvestigation] = useState<
    Awaited<ReturnType<typeof fetchTrustInvestigationDetail>> | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchTrustInvestigationDetail(id);
    setInvestigation(data);
  }, [id]);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'));
  }, [load]);

  async function handleResolve(decision: 'KEEP' | 'DELIST') {
    if (!session?.accessToken) return;
    const notes = window.prompt(decision === 'DELIST' ? 'Delist reason (optional):' : 'Admin notes (optional):') ?? '';
    setBusy(true);
    setError(null);
    try {
      await resolveTrustInvestigation(id, { decision, notes: notes || undefined }, session.accessToken);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Resolve failed');
    } finally {
      setBusy(false);
    }
  }

  if (!investigation && !error) {
    return <div className="min-h-screen bg-[#050508] p-8 text-zinc-500">Loading investigation…</div>;
  }

  if (error && !investigation) {
    return (
      <main className="min-h-screen bg-[#050508] p-8 text-red-300">
        {error}
        <Link href="/trust-center?tab=investigations" className="mt-4 block text-emerald-400">
          ← Trust Center
        </Link>
      </main>
    );
  }

  const inv = investigation as TrustInvestigation & {
    reports: Array<{
      id: string;
      category: keyof typeof VALIDATION_LABELS;
      comment: string | null;
      evidenceUrl: string | null;
      voteWeight: number;
      createdAt: string;
      user: { id: string; name: string | null; contributorLevel: number };
    }>;
  };

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/trust-center?tab=investigations" className="text-xs text-zinc-500 hover:text-white">
              ← Investigations
            </Link>
            <h1 className="mt-1 text-2xl font-bold">{inv.project.name}</h1>
            <p className="text-sm text-zinc-400">${inv.project.ticker} · {inv.status.replace(/_/g, ' ')}</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-6 px-6 py-8">
        {error && (
          <p className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm text-red-200">{error}</p>
        )}

        <div className="rounded-2xl border border-amber-500/30 bg-amber-950/10 p-5">
          <p className="text-sm text-zinc-300">{inv.reason ?? 'Community investigation'}</p>
          <p className="mt-3 text-sm">
            Trust {inv.trustScore}% · Suspicious {inv.scamScore}% · closes{' '}
            {new Date(inv.closesAt).toLocaleString()}
          </p>
          <Link href={`/project/${inv.project.slug}`} className="mt-3 inline-block text-sm text-emerald-400 hover:underline">
            View project room →
          </Link>
        </div>

        {isAdmin && (inv.status === 'ACTIVE' || inv.status === 'ADMIN_REVIEW') && (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => void handleResolve('KEEP')}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold disabled:opacity-50"
            >
              Keep listed
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void handleResolve('DELIST')}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold disabled:opacity-50"
            >
              Delist project
            </button>
          </div>
        )}

        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-500">
            Community reports ({inv.reports?.length ?? 0})
          </h2>
          <ul className="mt-4 space-y-3">
            {(inv.reports ?? []).map((report) => (
              <li key={report.id} className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded bg-zinc-800 px-2 py-0.5 font-medium text-zinc-200">
                    {VALIDATION_LABELS[report.category] ?? report.category}
                  </span>
                  <span className="text-zinc-500">
                    {report.user.name ?? 'Member'} · {contributorLevelLabel(report.user.contributorLevel)} · weight{' '}
                    {report.voteWeight}
                  </span>
                </div>
                {report.comment && <p className="mt-2 text-sm text-zinc-300">{report.comment}</p>}
                {report.evidenceUrl && (
                  <a
                    href={report.evidenceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-block text-xs text-sky-400 hover:underline"
                  >
                    Evidence ↗
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}

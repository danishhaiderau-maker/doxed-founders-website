'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  FOUNDER_VERIFICATION_LABELS,
  FounderVerificationCriterion,
} from '@dcf/utils';
import {
  fetchPendingApplications,
  PendingApplication,
  reviewListingApplication,
} from '@/lib/api';

export default function AdminApplicationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [items, setItems] = useState<PendingApplication[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [publishedSlug, setPublishedSlug] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  async function load(authToken: string) {
    try {
      setItems(await fetchPendingApplications(authToken));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    }
  }

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/applications');
      return;
    }
    if (!isAdmin) {
      setError('Admin access required. Sign in with an admin account.');
      return;
    }
    if (token) {
      load(token);
    }
  }, [status, isAdmin, token, router]);

  async function handleReview(id: string, reviewStatus: 'APPROVED' | 'REJECTED') {
    if (!token) return;
    setBusyId(id);
    setSuccess(null);
    setPublishedSlug(null);
    try {
      const result = await reviewListingApplication(id, reviewStatus, token);
      if (result.published) {
        setPublishedSlug(result.published.projectSlug);
        setSuccess(`Published ${result.published.projectName} to the curated directory.`);
      } else if (reviewStatus === 'REJECTED') {
        setSuccess('Application rejected.');
      }
      await load(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Review failed');
    } finally {
      setBusyId(null);
    }
  }

  if (status === 'loading') {
    return <main className="min-h-screen px-6 py-12 text-[var(--color-muted)]">Loading…</main>;
  }

  if (!isAdmin) {
    return (
      <main className="min-h-screen px-6 py-12">
        <div className="mx-auto max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-8 text-center">
          <h1 className="text-xl font-semibold">Admin access required</h1>
          <p className="mt-3 text-sm text-[var(--color-muted)]">{error}</p>
          <Link href="/login?callbackUrl=/admin/applications" className="mt-6 inline-block text-[var(--color-accent)]">
            Sign in as admin →
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen px-6 py-12">
      <div className="mx-auto max-w-5xl">
        <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
          ← Home
        </Link>
        <h1 className="mt-6 text-2xl font-bold">Pending listing requests</h1>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Signed in as {session?.user?.email}. Sorted by verification score.
        </p>

        {error && <p className="mt-4 text-sm text-[var(--color-danger)]">{error}</p>}
        {success && (
          <p className="mt-4 text-sm text-emerald-300">
            {success}
            {publishedSlug && (
              <>
                {' '}
                <Link href={`/project/${publishedSlug}`} className="underline hover:text-white">
                  View live project →
                </Link>
              </>
            )}
          </p>
        )}

        <div className="mt-8 space-y-4">
          {items.length === 0 && !error && (
            <p className="text-[var(--color-muted)]">No pending requests yet.</p>
          )}
          {items.map((item) => {
            const criteria = (item.verificationCriteria ?? []) as FounderVerificationCriterion[];
            const eligible = item.verificationScore >= 1;

            return (
              <div
                key={item.id}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5"
              >
                <div className="flex flex-col gap-4 sm:flex-row">
                  {item.logoUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={item.logoUrl} alt="" className="h-14 w-14 rounded-full" />
                  )}
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-3">
                      <h2 className="text-lg font-semibold">
                        {item.projectName}{' '}
                        <span className="text-[var(--color-muted)]">({item.ticker})</span>
                      </h2>
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                          eligible
                            ? 'bg-emerald-950/50 text-[var(--color-success)]'
                            : 'bg-amber-950/40 text-amber-300'
                        }`}
                      >
                        {item.verificationScore}/6 {eligible ? '· Eligible' : '· Insufficient'}
                      </span>
                    </div>
                    <ul className="mt-3 flex flex-wrap gap-2">
                      {criteria.map((c) => (
                        <li
                          key={c}
                          className="rounded-md bg-[var(--color-background)] px-2 py-1 text-xs text-white"
                        >
                          {FOUNDER_VERIFICATION_LABELS[c]}
                        </li>
                      ))}
                    </ul>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={busyId === item.id}
                        onClick={() => handleReview(item.id, 'APPROVED')}
                        className="rounded-lg bg-[var(--color-success)]/90 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        disabled={busyId === item.id}
                        onClick={() => handleReview(item.id, 'REJECTED')}
                        className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-muted)] hover:text-white disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </main>
  );
}

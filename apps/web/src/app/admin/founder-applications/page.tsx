'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  fetchPendingFounderApplications,
  reviewFounderApplication,
  type PendingFounderApplication,
} from '@/lib/api';

function formatDate(value: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function externalLink(href: string | null, label: string) {
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[var(--color-accent)] underline hover:text-white"
    >
      {label}
    </a>
  );
}

export default function AdminFounderApplicationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [items, setItems] = useState<PendingFounderApplication[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notesById, setNotesById] = useState<Record<string, string>>({});

  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  async function load(authToken: string) {
    try {
      const pending = await fetchPendingFounderApplications(authToken);
      setItems(pending);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    }
  }

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/founder-applications');
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

  async function handleReview(
    item: PendingFounderApplication,
    reviewStatus: 'APPROVED' | 'REJECTED',
  ) {
    if (!token) return;
    setBusyId(item.id);
    setSuccess(null);
    setError(null);
    try {
      await reviewFounderApplication(item.id, reviewStatus, token, {
        reviewNotes: notesById[item.id],
      });
      setSuccess(
        reviewStatus === 'APPROVED'
          ? `Approved ${item.projectName}. Builder tier flipped to VERIFIED_BUILDER.`
          : `Rejected ${item.projectName}.`,
      );
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
          <Link
            href="/login?callbackUrl=/admin/founder-applications"
            className="mt-6 inline-block text-[var(--color-accent)]"
          >
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
        <h1 className="mt-6 text-2xl font-bold">Doxxing applications</h1>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Visitor → Doxxed Builder upgrade requests. Founder reviews personally — no
          automated KYC, no third-party gate. Approving flips the founder to
          VERIFIED_BUILDER and unlocks launch rights + uncapped AI.{' '}
          <Link
            href="/admin/applications"
            className="text-[var(--color-accent)] underline hover:text-white"
          >
            View listing inbox →
          </Link>
        </p>

        {error && <p className="mt-4 text-sm text-[var(--color-danger)]">{error}</p>}
        {success && <p className="mt-4 text-sm text-emerald-300">{success}</p>}

        <div className="mt-8 space-y-4">
          {items.length === 0 && !error && (
            <p className="text-[var(--color-muted)]">No pending doxxing applications.</p>
          )}
          {items.map((item) => {
            const busy = busyId === item.id;
            return (
              <div
                key={item.id}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold">{item.projectName}</h2>
                    <p className="mt-1 text-xs text-[var(--color-muted)]">
                      Submitted {formatDate(item.createdAt)}
                    </p>
                  </div>
                  <span className="rounded-full bg-yellow-500/15 px-3 py-1 text-xs text-yellow-300">
                    {item.status}
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <div className="text-xs uppercase text-[var(--color-muted)]">Applicant</div>
                    <div className="mt-1">
                      {item.user?.name || item.user?.email || item.userId}
                    </div>
                    {item.user?.email && (
                      <a
                        href={`mailto:${item.user.email}`}
                        className="text-xs text-[var(--color-accent)] underline hover:text-white"
                      >
                        {item.user.email}
                      </a>
                    )}
                  </div>
                  <div>
                    <div className="text-xs uppercase text-[var(--color-muted)]">Twitter</div>
                    <div className="mt-1">
                      {item.twitterHandle || item.user?.twitterHandle || '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase text-[var(--color-muted)]">GitHub</div>
                    <div className="mt-1">
                      {externalLink(item.githubUrl, item.githubUrl || '—') ?? '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase text-[var(--color-muted)]">Founder video</div>
                    <div className="mt-1">
                      {externalLink(item.videoUrl, 'Watch video') ?? '—'}
                    </div>
                  </div>
                  {item.websiteUrl && (
                    <div>
                      <div className="text-xs uppercase text-[var(--color-muted)]">Website</div>
                      <div className="mt-1">
                        {externalLink(item.websiteUrl, item.websiteUrl) ?? '—'}
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-4">
                  <div className="text-xs uppercase text-[var(--color-muted)]">Idea</div>
                  <p className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-sm">
                    {item.ideaDescription}
                  </p>
                </div>

                <div className="mt-4">
                  <label className="text-xs uppercase text-[var(--color-muted)]">
                    Review notes (optional)
                  </label>
                  <textarea
                    className="mt-1 w-full rounded border border-[var(--color-border)] bg-transparent p-2 text-sm"
                    rows={2}
                    placeholder="Internal note for the founder review record."
                    value={notesById[item.id] ?? ''}
                    onChange={(e) =>
                      setNotesById((prev) => ({ ...prev, [item.id]: e.target.value }))
                    }
                    disabled={busy}
                  />
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                    onClick={() => handleReview(item, 'APPROVED')}
                    disabled={busy}
                  >
                    Approve & flip to VERIFIED_BUILDER
                  </button>
                  <button
                    type="button"
                    className="rounded border border-red-500/40 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                    onClick={() => handleReview(item, 'REJECTED')}
                    disabled={busy}
                  >
                    Reject
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </main>
  );
}

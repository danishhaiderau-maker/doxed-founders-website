'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  AdminApplicationUpdates,
  fetchPendingApplications,
  PendingApplication,
  reviewListingApplication,
} from '@/lib/api';
import {
  ApplicationReviewCard,
  applicationToReviewPayload,
  createReviewFormState,
} from './application-review-card';

export default function AdminApplicationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [items, setItems] = useState<PendingApplication[]>([]);
  const [forms, setForms] = useState<Record<string, AdminApplicationUpdates>>({});
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [publishedSlug, setPublishedSlug] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  async function load(authToken: string) {
    try {
      const pending = await fetchPendingApplications(authToken);
      setItems(pending);
      setForms(createReviewFormState(pending));
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

  function toggleExpanded(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  async function handleReview(id: string, reviewStatus: 'APPROVED' | 'REJECTED') {
    if (!token) return;
    const form = forms[id];
    if (!form) return;

    if (reviewStatus === 'APPROVED' && !expandedIds.has(id)) {
      setExpandedIds((prev) => new Set(prev).add(id));
      setError('Expand the application and review all details before approving.');
      return;
    }

    setBusyId(id);
    setSuccess(null);
    setPublishedSlug(null);
    setError(null);
    try {
      const result = await reviewListingApplication(id, reviewStatus, token, {
        reviewNotes: reviewNotes[id],
        updates: applicationToReviewPayload(form),
      });
      if (result.published) {
        setPublishedSlug(result.published.projectSlug);
        setSuccess(`Published ${result.published.projectName} to the curated directory.`);
      } else if (reviewStatus === 'REJECTED') {
        setSuccess('Application rejected.');
      }
      setExpandedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
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
        <h1 className="mt-6 text-2xl font-bold">Listing inbox</h1>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Signed in as {session?.user?.email}. Includes listings in 48h community vote (fast-track
          approve anytime) and items ready after voting ends. Expand to edit fields before publish.
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
          {items.map((item) => (
            <ApplicationReviewCard
              key={item.id}
              item={item}
              expanded={expandedIds.has(item.id)}
              busy={busyId === item.id}
              reviewNotes={reviewNotes[item.id] ?? ''}
              form={forms[item.id] ?? {}}
              onToggle={() => toggleExpanded(item.id)}
              onNotesChange={(value) =>
                setReviewNotes((prev) => ({ ...prev, [item.id]: value }))
              }
              onFormChange={(updates) =>
                setForms((prev) => ({ ...prev, [item.id]: updates }))
              }
              onApprove={() => handleReview(item.id, 'APPROVED')}
              onReject={() => handleReview(item.id, 'REJECTED')}
            />
          ))}
        </div>
      </div>
    </main>
  );
}

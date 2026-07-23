'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import { useSession } from 'next-auth/react';

/**
 * One-time MCQ consent pop-up for the Debug Squasher daily health check.
 *
 * Shows when the logged-in user has `debugSquasherConsent === 'unset'` (the
 * default). Three options:
 *   - Yes     → records 'accepted' (cron runs daily)
 *   - No      → records 'declined' (cron stays off; pop-up never resurfaces)
 *   - Later   → records 'later'    (pop-up resurfaces next session)
 *
 * Designed as a modal overlay so it can be dropped into the Founder OS shell.
 * Visibility is controlled by the parent (mount only when `open` is true).
 */
export function ConsentPopup() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkConsent = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(apiUrl('/api/debug-squasher/consent'), {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;
      const data = (await res.json()) as { consent?: string };
      // Only surface the pop-up for users who haven't answered yet.
      if (!data.consent || data.consent === 'unset') {
        setOpen(true);
      }
    } catch {
      // Network glitch — don't block the dashboard on a consent check.
    }
  }, [token]);

  useEffect(() => {
    if (status === 'authenticated') void checkConsent();
  }, [status, checkConsent]);

  async function recordChoice(choice: 'accepted' | 'declined' | 'later') {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(apiUrl('/api/debug-squasher/consent'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ choice }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(txt || `Request failed (${res.status})`);
      }
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to record choice');
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="debug-squasher-consent-title"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-2xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl">
        <div className="mb-3 flex items-center gap-2">
          <span className="rounded-md bg-emerald-500/15 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
            Phase 6.5
          </span>
          <span className="text-[11px] uppercase tracking-[0.18em] text-zinc-500">
            Founder OS · Daily Health Check
          </span>
        </div>

        <h2
          id="debug-squasher-consent-title"
          className="mb-3 text-xl font-semibold text-zinc-100"
        >
          Founder OS can run a daily health check that finds bugs before you do. Enable Debug Squasher?
        </h2>

        <p className="mb-5 text-sm leading-relaxed text-zinc-400">
          Each day at 06:00 UTC, Debug Squasher runs the full platform demo harness, sends any
          failures to Founder AI (DeepSeek V4 Pro for reasoning and V4 Flash for fast triage), and produces
          a diagnosis + suggested fix. You can disable it any time from{' '}
          <span className="text-zinc-200">Admin → Debug Squasher</span>.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => recordChoice('later')}
            className="rounded-md border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition hover:border-zinc-600 hover:text-zinc-100 disabled:opacity-50"
          >
            Later
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => recordChoice('declined')}
            className="rounded-md border border-zinc-700 px-4 py-2 text-sm text-zinc-300 transition hover:border-zinc-600 hover:text-zinc-100 disabled:opacity-50"
          >
            No, skip daily runs
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => recordChoice('accepted')}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Yes, enable'}
          </button>
        </div>
      </div>
    </div>
  );
}

'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiUrl } from '@/lib/api-base';
import { VERDICT_META, type IdeaCheck } from './types';

type Props = { accessToken: string };

const DISMISS_KEY = 'dcf-idea-pop-up-dismissed-at';
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

/**
 * IdeaPopUp — the daily proactive pop-up.
 *
 * Appears once per day when there's an unviewed COMPLETED IdeaCheck.
 * Dismissible; the dismissal is remembered in localStorage for 24h so it
 * doesn't reappear on every page nav. The backend `viewed` flag is the
 * source of truth — this component just suppresses within a session.
 *
 * Copy: "I ran a check on GitHub and the web for your idea — [N] similar
 * projects found. Your differentiation score: [X]/100. [View report]"
 */
export function IdeaPopUp({ accessToken }: Props) {
  const [check, setCheck] = useState<IdeaCheck | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    // Respect a recent client-side dismissal.
    try {
      const dismissedAt = Number(localStorage.getItem(DISMISS_KEY) ?? 0);
      if (Date.now() - dismissedAt < ONE_DAY_MS) {
        setHidden(true);
        return;
      }
    } catch {
      // localStorage unavailable — proceed
    }

    let cancelled = false;
    fetch(apiUrl('/api/idea-validator/latest-for-user'), {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((row: IdeaCheck | null) => {
        if (cancelled) return;
        if (row && row.status === 'COMPLETED' && !row.viewed && !row.dismissed) {
          setCheck(row);
        }
      })
      .catch(() => {
        // best-effort — pop-up is non-critical
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  const dismiss = useCallback(async () => {
    setHidden(true);
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      // ignore
    }
    // Mark viewed on the backend so it doesn't resurface next session either.
    if (check) {
      void fetch(apiUrl(`/api/idea-validator/check/${check.id}`), {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ viewed: true }),
      }).catch(() => {});
    }
  }, [accessToken, check]);

  if (hidden || !check) return null;

  const report = check.resultJson;
  const score = report?.differentiationScore ?? check.differentiationScore ?? 0;
  const count = report?.competitors?.length ?? 0;
  const verdict = report?.verdict ? VERDICT_META[report.verdict] : null;

  return (
    <div className="fixed bottom-6 right-6 z-50 w-80 rounded-xl border border-violet-500/40 bg-zinc-950/95 p-4 shadow-2xl backdrop-blur">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">{verdict?.emoji ?? '🔍'}</span>
          <span className="text-xs font-semibold uppercase tracking-wide text-violet-300">
            Idea check ready
          </span>
        </div>
        <button
          onClick={dismiss}
          className="text-zinc-600 transition hover:text-zinc-300"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
      <p className="mt-2 text-sm text-zinc-300">
        I ran a check on GitHub and the web for your idea —{' '}
        <span className="font-semibold text-white">{count} similar project{count === 1 ? '' : 's'}</span>{' '}
        found. Your differentiation score:{' '}
        <span className="font-semibold text-white">{score}/100</span>.
      </p>
      <div className="mt-3 flex items-center gap-3">
        <Link
          href="/founder-os"
          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-500"
        >
          View report →
        </Link>
        <button
          onClick={dismiss}
          className="text-xs text-zinc-500 transition hover:text-zinc-300"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

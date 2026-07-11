'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchTokenLaunchEligibility,
  type TokenLaunchEligibility,
} from '@/lib/api';

/**
 * LaunchEligibilityCard — shows the founder's pledge progress vs the 100K
 * DDollar threshold. "You need X more DDollar in community pledges to launch."
 *
 * YC Demo Day meets Kickstarter: progress bar, threshold countdown, checklist
 * surface. Pulses green when threshold met.
 */
export function LaunchEligibilityCard({ projectId }: { projectId: string }) {
  const [elig, setElig] = useState<TokenLaunchEligibility | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchTokenLaunchEligibility(projectId);
      setElig(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load eligibility');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6 text-sm text-zinc-500">
        Loading launch eligibility…
      </div>
    );
  }

  if (error || !elig) {
    return (
      <div className="rounded-2xl border border-red-500/30 bg-red-950/20 p-6 text-sm text-red-200">
        {error ?? 'Eligibility unavailable.'}
      </div>
    );
  }

  const pct =
    elig.threshold > 0
      ? Math.min(100, Math.round((elig.pledged / elig.threshold) * 100))
      : 0;
  const launched = elig.status !== 'PLEDGING';

  return (
    <div
      className={`rounded-2xl border p-6 transition-colors ${
        elig.thresholdMet
          ? 'border-emerald-500/40 bg-emerald-950/15'
          : 'border-zinc-800 bg-zinc-950/40'
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Token Launch
            </span>
            <span className="rounded bg-violet-950/40 px-2 py-0.5 text-[10px] uppercase text-violet-300">
              Phase 8
            </span>
          </div>
          <h3 className="mt-1 text-lg font-semibold text-zinc-100">
            {launched
              ? 'Token released'
              : elig.thresholdMet
                ? 'Threshold met — ready to launch'
                : 'Community pledge threshold'}
          </h3>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-zinc-100">
            {elig.pledged.toLocaleString()}
          </div>
          <div className="text-xs text-zinc-500">
            / {elig.threshold.toLocaleString()} DDollar
          </div>
        </div>
      </div>

      <div className="mt-4 h-3 w-full overflow-hidden rounded-full bg-zinc-900">
        <div
          className={`h-full rounded-full transition-all duration-700 ${
            elig.thresholdMet
              ? 'bg-gradient-to-r from-emerald-500 to-teal-400'
              : 'bg-gradient-to-r from-violet-500 to-fuchsia-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <p className="mt-3 text-sm text-zinc-400">
        {launched ? (
          <span>
            Status: <span className="text-zinc-200">{elig.status}</span> · Token
            is live.
          </span>
        ) : elig.thresholdMet ? (
          <span className="text-emerald-300">
            100K threshold met. You can release your token now.
          </span>
        ) : (
          <span>
            You need{' '}
            <span className="font-semibold text-zinc-200">
              {elig.needed.toLocaleString()} more DDollar
            </span>{' '}
            in community pledges to launch.
          </span>
        )}
      </p>

      <div className="mt-5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <ChecklistItem
          label="Founder doxxed"
          done={elig.checklist.founderDoxxed}
        />
        <ChecklistItem
          label="Build post shipped"
          done={elig.checklist.hasBuildPost}
        />
        <ChecklistItem
          label="Project complete"
          done={elig.checklist.projectComplete}
        />
        <ChecklistItem
          label="Twitter handle"
          done={elig.checklist.twitterHandle}
        />
      </div>

      <p className="mt-3 text-[11px] text-zinc-600">
        {elig.pledgePoolPercent}% of launched supply is reserved for pledgers,
        distributed pro-rata to their pledge.
      </p>
    </div>
  );
}

function ChecklistItem({ label, done }: { label: string; done: boolean }) {
  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-2 py-1.5 ${
        done
          ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-300'
          : 'border-zinc-800 bg-zinc-900/40 text-zinc-500'
      }`}
    >
      <span>{done ? '✓' : '○'}</span>
      <span>{label}</span>
    </div>
  );
}

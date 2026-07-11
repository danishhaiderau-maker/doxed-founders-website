'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchTokenLaunchEligibility,
  initiateTokenLaunch,
  type TokenLaunchEligibility,
} from '@/lib/api';

/**
 * LaunchButton — the big "🚀 I'm ready to launch my token" CTA.
 * Disabled until the 100K DDollar threshold is met AND the pre-launch
 * checklist is complete. On click, opens a confirmation modal explaining
 * the 15-day commitment window + 5% pledge allocation, then calls
 * POST /api/token-launch/:projectId/initiate.
 */
export function LaunchButton({
  projectId,
  accessToken,
  onLaunched,
}: {
  projectId: string;
  accessToken: string;
  onLaunched?: (launchId: string) => void;
}) {
  const [elig, setElig] = useState<TokenLaunchEligibility | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchTokenLaunchEligibility(projectId);
      setElig(data);
    } catch {
      // eligibility card surfaces its own error; we just stay disabled
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const canLaunch =
    elig?.thresholdMet && elig?.checklistComplete && elig?.status === 'PLEDGING';

  const alreadyLaunched =
    elig && elig.status !== 'PLEDGING' && elig.status !== 'CLOSED';

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const result = await initiateTokenLaunch(projectId, accessToken);
      setModalOpen(false);
      onLaunched?.(result.launchId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Launch failed');
    } finally {
      setSubmitting(false);
    }
  }

  if (alreadyLaunched) {
    return (
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 px-4 py-3 text-sm text-emerald-200">
        🚀 Token released — view the launch panel below.
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        disabled={!canLaunch}
        onClick={() => setModalOpen(true)}
        className={`group relative w-full overflow-hidden rounded-xl px-6 py-4 text-base font-semibold transition-all ${
          canLaunch
            ? 'bg-gradient-to-r from-violet-600 via-fuchsia-600 to-rose-500 text-white shadow-lg shadow-fuchsia-900/30 hover:shadow-fuchsia-700/40 hover:brightness-110'
            : 'cursor-not-allowed bg-zinc-900 text-zinc-600'
        }`}
      >
        <span className="relative z-10 flex items-center justify-center gap-2">
          <span className={canLaunch ? 'animate-pulse' : ''}>🚀</span>
          {canLaunch
            ? "I'm ready to launch my token"
            : 'Token launch locked'}
        </span>
        {!canLaunch && elig && (
          <span className="relative z-10 mt-1 block text-[11px] font-normal text-zinc-600">
            {elig.needed > 0
              ? `${elig.needed.toLocaleString()} DDollar to threshold`
              : !elig.checklistComplete
                ? 'Complete pre-launch checklist'
                : 'Unavailable'}
          </span>
        )}
      </button>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-lg rounded-2xl border border-zinc-700 bg-zinc-950 p-6 shadow-2xl">
            <h3 className="text-lg font-semibold text-zinc-100">
              Release your token on Solana devnet
            </h3>
            <p className="mt-2 text-sm text-zinc-400">
              This will mint your SPL token and open a 15-day community
              commitment window.
            </p>

            <ul className="mt-4 space-y-2 text-sm text-zinc-300">
              <li className="flex gap-2">
                <span className="text-violet-400">•</span>
                <span>
                  <span className="font-semibold">5% pledge pool:</span>{' '}
                  {elig?.pledgePoolPercent ?? 5}% of supply reserved for your
                  pledgers, distributed pro-rata.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-violet-400">•</span>
                <span>
                  <span className="font-semibold">15-day window:</span> the
                  community can keep committing DDollar until the window closes,
                  then allocations finalize.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-violet-400">•</span>
                <span>
                  <span className="font-semibold">Solana devnet:</span> this is
                  a devnet mint (no real value). You&apos;ll migrate to mainnet
                  when ready.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-violet-400">•</span>
                <span>
                  <span className="font-semibold">0.1% DEX fee:</span> every
                  post-launch swap accrues a 0.1% fee to the platform treasury.
                </span>
              </li>
            </ul>

            {error && (
              <div className="mt-4 rounded-md border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm text-red-200">
                {error}
              </div>
            )}

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                disabled={submitting}
                onClick={() => {
                  setModalOpen(false);
                  setError(null);
                }}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-900 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={handleConfirm}
                className="rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 px-4 py-2 text-sm font-semibold text-white hover:brightness-110 disabled:opacity-50"
              >
                {submitting ? 'Minting on devnet…' : '🚀 Release token'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

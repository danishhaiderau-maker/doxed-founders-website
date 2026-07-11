'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  fetchFounderDashboard,
  fetchProjectRoom,
  fetchTokenLaunchEligibility,
  type TokenLaunchEligibility,
} from '@/lib/api';

/**
 * LaunchTeaserCard — sits at the top of the Raise Room discovery hub for
 * signed-in founders. Resolves the founder's primary project, pulls its
 * launch eligibility, and offers a one-click jump into the project-specific
 * launch flow.
 *
 * If the founder has no project yet, points them at Founder Den to create one.
 */
export function LaunchTeaserCard({ accessToken }: { accessToken: string }) {
  const [elig, setElig] = useState<TokenLaunchEligibility | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const dash = await fetchFounderDashboard(accessToken);
      if (!dash.primaryProjectSlug) {
        return;
      }
      const room = await fetchProjectRoom(dash.primaryProjectSlug, accessToken);
      setProjectId(room.id);
      setProjectName(room.name);
      try {
        const e = await fetchTokenLaunchEligibility(room.id);
        setElig(e);
      } catch {
        // eligibility optional — the card still links to the panel
      }
    } catch {
      // user is a visitor or hasn't built a profile — card stays hidden
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading || !projectId) return null;

  const launched = elig && elig.status !== 'PLEDGING';
  const thresholdMet = elig?.thresholdMet ?? false;

  return (
    <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-zinc-950/70 to-fuchsia-950/30 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
              Phase 8 · Token Launch
            </span>
            {launched && (
              <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold uppercase text-emerald-300">
                {elig?.status}
              </span>
            )}
          </div>
          <h2 className="mt-1 text-xl font-bold text-white">
            {projectName ?? 'Your project'}
          </h2>
          <p className="mt-1 text-sm text-zinc-400">
            {launched
              ? `Token is ${elig?.status}. Open the launch panel for the live mint + commitment window.`
              : thresholdMet
                ? '100K DDollar threshold met — release your token on Solana devnet.'
                : elig
                  ? `${elig.needed.toLocaleString()} DDollar to the launch threshold. Share your project to rally pledgers.`
                  : 'Open the launch panel to track pledges and release your token.'}
          </p>
        </div>
        <Link
          href={`/raise-room/${projectId}`}
          className="shrink-0 rounded-xl bg-gradient-to-r from-violet-600 via-fuchsia-600 to-rose-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-fuchsia-900/30 hover:brightness-110"
        >
          {launched ? 'View launch →' : '🚀 Open launch panel'}
        </Link>
      </div>

      {elig && !launched && (
        <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-zinc-900">
          <div
            className="h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-all"
            style={{
              width: `${Math.min(100, Math.round((elig.pledged / elig.threshold) * 100))}%`,
            }}
          />
        </div>
      )}
    </section>
  );
}

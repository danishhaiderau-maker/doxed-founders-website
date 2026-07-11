'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchTokenLaunchEligibility,
  type TokenLaunchEligibility,
} from '@/lib/api';
import { LaunchEligibilityCard } from './launch-eligibility-card';
import { LaunchButton } from './launch-button';
import { PledgeLeaderboard } from './pledge-leaderboard';
import { LaunchProgress } from './launch-progress';
import { DexPanel } from './dex-panel';

/**
 * TokenLaunchPanel — the full Phase 8 Raise Room → Token Launch surface for
 * a single project. Combines:
 *
 *   - LaunchEligibilityCard (progress vs 100K threshold)
 *   - LaunchButton (founder CTA + confirmation modal)
 *   - PledgeLeaderboard (top pledgers with projected allocation)
 *   - LaunchProgress (15-day window + mint address, once released)
 *   - DexPanel (post-launch swap UI)
 *
 * The panel handles the PLEDGING → WINDOW_OPEN → LIVE transitions by polling
 * eligibility on mount; once a launchId exists, the progress + dex panels
 * take over the live state.
 */
export function TokenLaunchPanel({
  projectId,
  accessToken,
}: {
  projectId: string;
  accessToken: string;
}) {
  const [elig, setElig] = useState<TokenLaunchEligibility | null>(null);
  const [activeLaunchId, setActiveLaunchId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchTokenLaunchEligibility(projectId);
      setElig(data);
      if (data.status !== 'PLEDGING') {
        setActiveLaunchId(data.launchId);
      }
    } catch {
      // surfaced by child cards
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const showLaunchPanel = Boolean(
    activeLaunchId || (elig && elig.status !== 'PLEDGING'),
  );
  const showDexPanel = elig?.status === 'LIVE';

  return (
    <section className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Raise Room · Token Launch
        </h2>
        <span className="text-[10px] uppercase text-violet-400">
          YC Demo Day meets Kickstarter
        </span>
      </div>

      <LaunchEligibilityCard projectId={projectId} />

      <LaunchButton
        projectId={projectId}
        accessToken={accessToken}
        onLaunched={(lid) => {
          setActiveLaunchId(lid);
          void refresh();
        }}
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <PledgeLeaderboard projectId={projectId} />
        {showLaunchPanel &&
          (activeLaunchId || elig?.launchId) && (
            <LaunchProgress launchId={activeLaunchId ?? elig!.launchId} />
          )}
      </div>

      {showDexPanel &&
        (activeLaunchId || elig?.launchId) && (
          <DexPanel
            launchId={activeLaunchId ?? elig!.launchId}
            accessToken={accessToken}
          />
        )}
    </section>
  );
}

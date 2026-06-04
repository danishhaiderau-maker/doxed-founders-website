'use client';

import { useCallback, useEffect, useState } from 'react';
import type { DeployIntelligenceCard, ProjectTimelineEntry } from '@dcf/utils';
import {
  fetchDeployIntelligence,
  fetchProjectTimeline,
  fetchDesktopBridge,
  type DesktopBridgeResponse,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

function riskClass(risk: DeployIntelligenceCard['risk']) {
  if (risk === 'high') return 'text-red-300';
  if (risk === 'medium') return 'text-amber-300';
  return 'text-emerald-300';
}

export function FounderProjectTimelinePanel({ accessToken }: Props) {
  const [entries, setEntries] = useState<ProjectTimelineEntry[]>([]);
  const [deployCards, setDeployCards] = useState<DeployIntelligenceCard[]>([]);
  const [bridge, setBridge] = useState<DesktopBridgeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [timeline, deploys, desktop] = await Promise.all([
        fetchProjectTimeline(accessToken),
        fetchDeployIntelligence(accessToken),
        fetchDesktopBridge(accessToken).catch(() => null),
      ]);
      setEntries(timeline.entries.slice(0, 12));
      setDeployCards(deploys.cards.slice(0, 3));
      setBridge(desktop);
    } catch {
      setEntries([]);
      setDeployCards([]);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-950/60 p-4">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Project timeline
        </p>
        {loading && entries.length === 0 ? (
          <p className="mt-3 text-xs text-zinc-600">Loading narrative…</p>
        ) : entries.length === 0 ? (
          <p className="mt-3 text-xs text-zinc-500">
            Ship merges, deploys, and updates — timeline fills from GitHub and founder events.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {entries.map((e) => (
              <li key={e.id} className="text-xs text-zinc-300">
                <span className="text-zinc-600">{e.at.slice(0, 10)}</span>
                <span className="mx-1.5 text-zinc-700">·</span>
                <span className="text-zinc-200">{e.title}</span>
                {e.commitCount != null && e.commitCount > 0 && (
                  <span className="ml-1 text-zinc-600">({e.commitCount} commits)</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {deployCards.length > 0 && (
        <div className="rounded-2xl border border-emerald-500/20 bg-emerald-950/10 p-4">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-emerald-300/90">
            Deployment intelligence
          </p>
          <ul className="mt-3 space-y-3">
            {deployCards.map((c) => (
              <li key={c.id} className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-3">
                <p className="text-xs font-medium text-white">{c.title}</p>
                <p className="mt-1 text-[11px] text-zinc-400">{c.impact}</p>
                {c.affectedRoutes.length > 0 && (
                  <p className="mt-1 text-[10px] text-zinc-500">
                    Routes: {c.affectedRoutes.join(' · ')}
                  </p>
                )}
                <p className={`mt-1 text-[10px] font-semibold ${riskClass(c.risk)}`}>
                  Risk: {c.risk}
                </p>
                {c.nextSteps[0] && (
                  <p className="mt-1 text-[10px] text-violet-300/90">Next: {c.nextSteps[0]}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {bridge?.latest && (
        <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-4">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Desktop bridge
          </p>
          <p className="mt-2 text-xs text-zinc-300">
            {bridge.latest.label}
            {bridge.latest.branch ? ` · ${bridge.latest.branch}` : ''}
          </p>
          {bridge.latest.taskLabel && (
            <p className="mt-1 text-[11px] text-zinc-500">Task: {bridge.latest.taskLabel}</p>
          )}
          {bridge.latest.openFilePaths && bridge.latest.openFilePaths.length > 0 && (
            <p className="mt-1 text-[10px] text-zinc-600 line-clamp-2">
              Files: {bridge.latest.openFilePaths.join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

'use client';

import { AGENT_HUB_POLL_SIM_LIVE_VIEW_MS, AGENT_HUB_POLL_SIM_MS } from '@/hooks/use-relay-sim-live-view';

export function RelaySimLiveViewToggle({
  enabled,
  onChange,
  simActive,
}: {
  enabled: boolean;
  onChange: (next: boolean) => void;
  simActive: boolean;
}) {
  if (!simActive) return null;

  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-zinc-700/80 bg-black/25 px-3 py-2.5 text-left">
      <input
        type="checkbox"
        checked={enabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-zinc-600 bg-zinc-900 text-sky-500 focus:ring-sky-500/40"
      />
      <span className="min-w-0">
        <span className="text-xs font-semibold text-white">Live view</span>
        <span className="mt-0.5 block text-[10px] leading-snug text-zinc-500">
          {enabled
            ? `Refreshes this screen every ${AGENT_HUB_POLL_SIM_LIVE_VIEW_MS / 1000}s while sim runs. Copy relay on Railway is unchanged.`
            : `Off — background refresh every ${AGENT_HUB_POLL_SIM_MS / 1000}s. Saved for your login on this browser only.`}
        </span>
      </span>
    </label>
  );
}

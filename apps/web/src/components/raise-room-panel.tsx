'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import {
  exportRaiseParticipants,
  lockRaiseSlots,
  ProjectRoom,
  RaiseAllocationLeaderboardEntry,
} from '@/lib/api';

type RaiseRoomPanelProps = {
  room: Pick<
    ProjectRoom,
    'slug' | 'name' | 'activeRaise' | 'allocationLeaderboard' | 'isProjectFounder'
  >;
  accessToken?: string;
  allocAmount: string;
  onAllocAmountChange: (v: string) => void;
  onAllocate: () => void;
  onMessage?: (msg: string) => void;
  onRefresh?: () => void;
};

function daysLeft(endsAt?: string | null) {
  if (!endsAt) return null;
  const diff = new Date(endsAt).getTime() - Date.now();
  return Math.max(0, Math.ceil(diff / 86400000));
}

export function RaiseRoomPanel({
  room,
  accessToken,
  allocAmount,
  onAllocAmountChange,
  onAllocate,
  onMessage,
  onRefresh,
}: RaiseRoomPanelProps) {
  const raise = room.activeRaise;
  if (!raise) return null;

  const demandPct = Math.min(100, Math.round((raise.totalAllocated / raise.goalUsd) * 100));
  const remaining = daysLeft(raise.endsAt);
  const slotsFull =
    raise.maxParticipantSlots != null && raise.allocatorCount >= raise.maxParticipantSlots;

  async function handleExport() {
    if (!accessToken) return;
    try {
      const data = await exportRaiseParticipants(raise!.id, accessToken);
      await navigator.clipboard.writeText(data.csv);
      onMessage?.(`Copied ${data.participantCount} participants for token distribution`);
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Export failed');
    }
  }

  async function handleLockSlots() {
    if (!accessToken) return;
    try {
      const result = await lockRaiseSlots(raise!.id, accessToken);
      onMessage?.(result.message);
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not lock slots');
    }
  }

  return (
    <div className="space-y-5 rounded-2xl border border-violet-500/35 bg-gradient-to-br from-violet-950/30 to-zinc-950 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-violet-400">Raise Room</p>
          <h3 className="mt-1 text-xl font-bold text-white">{room.name}</h3>
          <p className="mt-1 text-sm text-zinc-400">
            Public demand validation · paper dollars locked · {raise.allocationFeePercent ?? 1}% burned on commit
          </p>
        </div>
        {'momentumScore' in raise && (
          <div className="rounded-xl border border-violet-500/30 bg-black/30 px-4 py-2 text-center">
            <p className="text-[10px] uppercase text-zinc-500">Momentum</p>
            <p className="text-2xl font-bold text-violet-300">{(raise as { momentumScore?: number }).momentumScore ?? demandPct}%</p>
          </div>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg bg-black/30 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Target</p>
          <p className="font-semibold text-white">{formatUsd(raise.goalUsd, 0)}</p>
        </div>
        <div className="rounded-lg bg-black/30 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Allocated</p>
          <p className="font-semibold text-emerald-300">{formatUsd(raise.totalAllocated, 0)}</p>
        </div>
        <div className="rounded-lg bg-black/30 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Participants</p>
          <p className="font-semibold text-white">
            {raise.allocatorCount}
            {raise.maxParticipantSlots != null ? ` / ${raise.maxParticipantSlots} slots` : ''}
          </p>
        </div>
        <div className="rounded-lg bg-black/30 px-3 py-2">
          <p className="text-[10px] uppercase text-zinc-600">Community token</p>
          <p className="font-semibold text-amber-200">
            {raise.communityTokenPercent ?? 10}% for Raise Room
          </p>
        </div>
      </div>

      <div>
        <div className="flex justify-between text-xs text-zinc-500">
          <span>Demand progress</span>
          <span>
            {demandPct}% · {remaining != null ? `${remaining}d left` : `${raise.durationDays}d window`}
          </span>
        </div>
        <div className="mt-1 h-3 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full bg-violet-500 transition-all" style={{ width: `${demandPct}%` }} />
        </div>
        {(raise.totalBurnedUsd ?? 0) > 0 && (
          <p className="mt-2 text-[11px] text-zinc-500">
            {formatUsd(raise.totalBurnedUsd ?? 0, 0)} paper dollars burned from circulation (allocation fees)
          </p>
        )}
      </div>

      {raise.slotsLocked ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">
          ICO slots locked — participants reserved for token distribution.
        </p>
      ) : accessToken ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="number"
            value={allocAmount}
            onChange={(e) => onAllocAmountChange(e.target.value)}
            disabled={slotsFull}
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
            placeholder="Paper dollars to allocate"
          />
          <button
            type="button"
            onClick={onAllocate}
            disabled={slotsFull}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {slotsFull ? 'Slots full' : 'Reserve ICO slot'}
          </button>
        </div>
      ) : (
        <Link href="/login" className="inline-block text-sm text-violet-300 underline">
          Sign in to allocate paper dollars
        </Link>
      )}

      <AllocationLeaderboard entries={room.allocationLeaderboard ?? []} />

      {room.isProjectFounder && accessToken && (
        <div className="flex flex-wrap gap-2 border-t border-zinc-800 pt-4">
          <button
            type="button"
            onClick={handleExport}
            className="rounded-lg border border-emerald-500/40 px-4 py-2 text-sm text-emerald-200"
          >
            Copy distribution list (1-click export)
          </button>
          {!raise.slotsLocked && (
            <button
              type="button"
              onClick={handleLockSlots}
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white"
            >
              Lock slots & close raise
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function AllocationLeaderboard({ entries }: { entries: RaiseAllocationLeaderboardEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        No public allocations yet — be the first to reserve an ICO slot with paper dollars.
      </p>
    );
  }

  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Public allocations</p>
      <ul className="mt-2 space-y-1">
        {entries.slice(0, 15).map((e) => (
          <li
            key={e.userId}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800/80 bg-black/20 px-3 py-2 text-sm"
          >
            <span className="text-zinc-300">
              <span className="text-zinc-600">#{e.rank}</span> {e.displayName}
              {e.walletAddress && (
                <span className="ml-2 text-[10px] text-sky-400">
                  {e.walletAddress.slice(0, 4)}…{e.walletAddress.slice(-4)}
                </span>
              )}
            </span>
            <span className="font-semibold text-violet-200">{formatUsd(e.amountUsd, 0)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { formatDdollarCompact } from '@dcf/utils';

function formatCountdown(ms: number) {
  if (ms <= 0) return 'Expired';
  const totalSec = Math.floor(ms / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

export function AgentRentalCountdown({
  expiresAt,
  compact = false,
  onRenew,
  renewBusy,
  costWeek,
}: {
  expiresAt: string;
  compact?: boolean;
  onRenew?: () => void;
  renewBusy?: boolean;
  costWeek?: number;
}) {
  const [remaining, setRemaining] = useState(() => new Date(expiresAt).getTime() - Date.now());

  useEffect(() => {
    const tick = () => setRemaining(new Date(expiresAt).getTime() - Date.now());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const expired = remaining <= 0;

  return (
    <div
      className={`rounded-xl border ${
        expired ? 'border-red-500/50 bg-red-950/30 ring-2 ring-red-500/25' : 'border-emerald-500/35 bg-emerald-950/20'
      } ${compact ? 'px-3 py-2' : 'px-4 py-3'}`}
    >
      <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Live copy rental</p>
      <p
        className={`mt-1 font-bold ${compact ? 'text-sm' : 'text-lg'} ${
          expired ? 'text-red-200' : 'text-emerald-200'
        }`}
      >
        {expired ? 'Rental expired — renew to resume live copy' : formatCountdown(remaining)}
      </p>
      {!compact && (
        <p className="mt-1 text-[11px] text-zinc-500">
          Expires {new Date(expiresAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
        </p>
      )}
      {expired && onRenew ? (
        <button
          type="button"
          disabled={renewBusy}
          onClick={onRenew}
          className="mt-3 w-full rounded-lg bg-red-600 py-2.5 text-sm font-bold text-white hover:bg-red-500 disabled:opacity-50"
        >
          {renewBusy
            ? 'Renewing…'
            : `Renew 1 week${costWeek ? ` · ${formatDdollarCompact(costWeek)}` : ''}`}
        </button>
      ) : null}
    </div>
  );
}

export function LiveCopyRentalBadge({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState(() => new Date(expiresAt).getTime() - Date.now());

  useEffect(() => {
    const tick = () => setRemaining(new Date(expiresAt).getTime() - Date.now());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const expired = remaining <= 0;

  return (
    <span
      className={`inline-flex items-center gap-2 rounded-xl border px-5 py-2.5 text-sm font-semibold ${
        expired
          ? 'border-red-500/50 bg-red-950/40 text-red-200'
          : 'border-emerald-500/40 bg-emerald-950/30 text-emerald-200'
      }`}
    >
      <span aria-hidden>⏱</span>
      {expired ? 'Rental expired — renew' : `${formatCountdown(remaining)} remaining`}
    </span>
  );
}

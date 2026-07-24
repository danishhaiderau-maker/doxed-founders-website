'use client';

import Link from 'next/link';
import { Gauge, KeyRound, Sparkles } from 'lucide-react';
import type { FounderPromoUserStatus } from '@/lib/api';

type Props = { status: FounderPromoUserStatus | null };

function quotaState(status: FounderPromoUserStatus | null) {
  if (!status) return { label: 'Checking', tone: 'text-zinc-300 bg-zinc-800' };
  if (!status.enabled) return { label: 'Unavailable', tone: 'text-zinc-300 bg-zinc-800' };
  if (status.exhausted) return { label: 'Used', tone: 'text-amber-200 bg-amber-500/15' };
  if (status.eligible) {
    const remaining = status.tokenCap > 0 ? status.tokensRemaining / status.tokenCap : 0;
    return remaining <= 0.2
      ? { label: 'Running low', tone: 'text-amber-200 bg-amber-500/15' }
      : { label: 'Available', tone: 'text-emerald-200 bg-emerald-500/15' };
  }
  return { label: 'Needs setup', tone: 'text-blue-200 bg-blue-500/15' };
}

export function FounderFreeQuotaCard({ status }: Props) {
  const usedPercent = status?.tokenCap
    ? Math.min(100, Math.max(0, Math.round((status.tokensUsed / status.tokenCap) * 100)))
    : 0;
  const state = quotaState(status);
  const planLabel = !status
    ? 'managed'
    : status.plan === 'builder'
      ? 'Builder'
      : status.plan === 'team'
        ? status.teamName ?? 'Team'
        : 'Free';

  return (
    <section className="border-b border-zinc-800 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-300">
            <Sparkles className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <h3 className="font-semibold text-white">Founder {planLabel} usage</h3>
            <p className="mt-1 max-w-xl text-sm leading-6 text-zinc-400">
              Managed DeepSeek usage is measured in weighted units. Personal provider profiles and local Ollama
              remain outside this allowance.
            </p>
          </div>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${state.tone}`}>{state.label}</span>
      </div>

      {status?.enabled && status.founderRegistered && (
        <div className="mt-5">
          <div className="flex items-center justify-between gap-3 text-xs text-zinc-400">
            <span>{usedPercent}% used</span>
            <span>{status.expiresAt ? `Renews ${new Date(status.expiresAt).toLocaleDateString()}` : 'Recurring weekly allowance'}</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-zinc-800" aria-label={`${usedPercent}% of free quota used`}>
            <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-300" style={{ width: `${usedPercent}%` }} />
          </div>
          {!status.eligible && status.message && <p className="mt-3 text-xs text-amber-200/90">{status.message}</p>}
          {status.reservedWeightedUnits > 0 && (
            <p className="mt-2 text-xs text-zinc-500">
              {status.reservedWeightedUnits.toLocaleString()} units are reserved by work currently running.
            </p>
          )}
        </div>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <div className="flex gap-3 rounded-lg border border-zinc-800 bg-zinc-950/45 p-4">
          <Gauge className="mt-0.5 h-4 w-4 shrink-0 text-blue-300" aria-hidden />
          <div>
            <p className="text-sm font-medium text-zinc-100">Managed by Founder OS</p>
            <p className="mt-1 text-xs leading-5 text-zinc-500">
              Founder OS selects a healthy, cost-efficient model for each task. Model changes do not change your quota view.
            </p>
          </div>
        </div>
        <div className="flex gap-3 rounded-lg border border-zinc-800 bg-zinc-950/45 p-4">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-violet-300" aria-hidden />
          <div>
            <p className="text-sm font-medium text-zinc-100">Personal and local AI</p>
            <p className="mt-1 text-xs leading-5 text-zinc-500">Your own provider keys and local models do not consume Founder Free quota.</p>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {status && !status.founderRegistered && (
          <Link href="/founder-den?tab=build" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500">
            Finish setup
          </Link>
        )}
        <Link href="/settings/builder?tab=ai" className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:border-zinc-500 hover:text-white">
          Personal AI
        </Link>
      </div>
    </section>
  );
}

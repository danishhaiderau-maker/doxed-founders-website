'use client';

import Link from 'next/link';
import { formatDdollar, formatDdollarCompact } from '@dcf/utils';

export type DdollarBalanceData = {
  userId: string;
  rawDdollar: number;
  reputationMultiplierInputs: {
    verifiedAccount: boolean;
    contributorLevel: number;
    reputationPoints: number;
    accountAgeDays: number;
    verifiedMilestoneCount: number;
    builderScore: number;
  } | null;
};

export function DdollarBalance({
  balance,
  signedIn,
  loading,
}: {
  balance: DdollarBalanceData | null;
  signedIn: boolean;
  loading: boolean;
}) {
  if (!signedIn) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">DDollar balance</h3>
        <p className="mt-3 text-sm text-zinc-400">
          <Link href="/login?callbackUrl=/founder-economics" className="text-emerald-400 hover:underline">
            Sign in
          </Link>{' '}
          to see your raw + reputation-weighted DDollar balance.
        </p>
      </section>
    );
  }

  const inputs = balance?.reputationMultiplierInputs;
  const weighted =
    balance && inputs
      ? Math.round(
          balance.rawDdollar *
            (1 + (inputs.contributorLevel - 1) * 0.1 + inputs.verifiedMilestoneCount * 0.1),
        )
      : balance?.rawDdollar ?? 0;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">DDollar balance</h3>
      {loading && !balance ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-zinc-500">Raw DDollar</span>
            <span className="text-lg font-semibold text-white">
              {formatDdollar(balance?.rawDdollar ?? 0)}
            </span>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-zinc-500">Reputation-weighted (preview)</span>
            <span className="text-lg font-semibold text-violet-300">
              {formatDdollarCompact(weighted)}
            </span>
          </div>
          {inputs && (
            <ul className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-zinc-500">
              <li>Verified: {inputs.verifiedAccount ? 'yes' : 'no'}</li>
              <li>Level: {inputs.contributorLevel}</li>
              <li>Account age: {inputs.accountAgeDays}d</li>
              <li>Milestones: {inputs.verifiedMilestoneCount}</li>
              <li>Builder score: {inputs.builderScore}</li>
              <li>Reputation pts: {inputs.reputationPoints}</li>
            </ul>
          )}
          <p className="pt-2 text-[11px] text-zinc-600">
            Trading and swaps never earn DDollar — Economic vs Speculative separation.
          </p>
        </div>
      )}
    </section>
  );
}

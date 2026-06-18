'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DDOLLAR_CURRENCY_NAME,
  formatDdollar,
  POINTS,
  POINT_ACTIONS,
  pointActionLabel,
  REFERRAL_REWARD_BLUE_VERIFIED,
  REFERRAL_REWARD_STANDARD,
} from '@dcf/utils';
import { ReferralPanel } from '@/components/account/referral-panel';
import {
  AccountOverview,
  AccountPointLedgerEntry,
  fetchAccountOverview,
  fetchAccountPointLedger,
} from '@/lib/api';

const WAYS_TO_EARN = [
  { label: 'Create account (welcome)', amount: POINTS.REGISTER, href: '/register' },
  { label: 'Refer a friend (X signup)', amount: REFERRAL_REWARD_STANDARD, href: '/ddollar#referral' },
  { label: 'Refer verified X account', amount: REFERRAL_REWARD_BLUE_VERIFIED, href: '/ddollar#referral' },
  { label: 'X verified welcome bonus', amount: POINTS.X_BLUE_VERIFIED, href: '/register' },
  { label: 'Vote on listing', amount: POINTS.LISTING_VOTE, href: '/trust-center?tab=scout-voting' },
  { label: 'Helpful review', amount: POINTS.VALIDATION_HELPFUL, href: '/trust-center?tab=reviews' },
  { label: 'Correct validation', amount: POINTS.VALIDATION_CORRECT, href: '/trust-center?tab=scout-voting' },
  { label: 'Confirmed scam report', amount: POINTS.SCAM_CONFIRMED, href: '/trust-center?tab=investigations' },
  { label: 'Daily login', amount: POINTS.DAILY_LOGIN, href: '/feed' },
  { label: 'Paper trade', amount: POINTS.PAPER_TRADE, href: '/paper-trading' },
  { label: 'Build update', amount: POINTS.FOUNDER_BUILD_POST, href: '/founder-den' },
  { label: 'Project listed (scout)', amount: POINTS.LISTING_SCOUT_APPROVED, href: '/list-your-project' },
] as const;

const WAYS_TO_SPEND = [
  { label: 'BTC Agent Rental', detail: 'Follow live trading agents', href: '/agent-hub' },
  { label: 'Paper Trading Top-Ups', detail: 'Boost paper desk balance', href: '/paper-trading' },
  { label: 'Premium Features', detail: 'Platform perks as they launch', href: '/rules' },
  { label: 'Future Platform Services', detail: 'More spend sinks coming', href: '/rules' },
] as const;

export function DdollarWalletPage() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [overview, setOverview] = useState<AccountOverview | null>(null);
  const [ledger, setLedger] = useState<AccountPointLedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [ov, lg] = await Promise.all([
        fetchAccountOverview(token),
        fetchAccountPointLedger(token, 100),
      ]);
      setOverview(ov);
      setLedger(lg);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load wallet');
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const stats = useMemo(() => {
    const lifetimeEarned = ledger.filter((e) => e.amount > 0).reduce((s, e) => s + e.amount, 0);
    const lifetimeSpent = ledger.filter((e) => e.amount < 0).reduce((s, e) => s + Math.abs(e.amount), 0);
    const pendingRewards = ledger
      .filter((e) => e.amount > 0 && e.actionKey.includes('PENDING'))
      .reduce((s, e) => s + e.amount, 0);
    return { lifetimeEarned, lifetimeSpent, pendingRewards };
  }, [ledger]);

  const recentLedger = ledger.slice(0, 12);

  if (!token) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
        <Link href="/login?callbackUrl=/ddollar" className="font-semibold underline">
          Sign in
        </Link>{' '}
        to view your {DDOLLAR_CURRENCY_NAME} wallet and earn rewards across the platform.
      </div>
    );
  }

  if (error && !overview) {
    return <p className="text-sm text-red-300">{error}</p>;
  }

  return (
    <div className="space-y-8">
      <section className="rounded-2xl border border-amber-500/25 bg-gradient-to-br from-amber-950/30 to-zinc-950 p-6">
        <p className="text-xs font-semibold uppercase tracking-wider text-amber-300/80">Wallet</p>
        <p className="mt-2 text-4xl font-bold text-white">
          {formatDdollar(overview?.reputation.reputationPoints ?? 0)}
        </p>
        <p className="mt-1 text-sm text-zinc-400">
          Current balance — in-game {DDOLLAR_CURRENCY_NAME} for participation, agents, and paper trading
        </p>
        {overview && overview.reputation.totalPoints > 0 && (
          <p className="mt-2 text-sm text-amber-200/90">
            {(
              (overview.reputation.reputationPoints / overview.reputation.totalPoints) *
              100
            ).toFixed(4)}
            % of platform circulating supply (
            {overview.reputation.totalPoints.toLocaleString()} {DDOLLAR_CURRENCY_NAME} total)
          </p>
        )}

        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          <Stat label="Lifetime earned" value={formatDdollar(stats.lifetimeEarned || overview?.reputation.reputationPoints || 0)} />
          <Stat label="Lifetime spent" value={formatDdollar(stats.lifetimeSpent)} />
          <Stat label="Pending rewards" value={formatDdollar(stats.pendingRewards)} />
        </div>
      </section>

      <div id="referral">
        <ReferralPanel accessToken={token} />
      </div>

      <div className="grid gap-8 lg:grid-cols-2">
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
          <h2 className="text-lg font-semibold text-white">Ways to earn</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Highlights — every credit is idempotent where noted and logged in your ledger below.
          </p>
          <ul className="mt-4 divide-y divide-zinc-800">
            {WAYS_TO_EARN.map((item) => (
              <li key={item.label}>
                <Link
                  href={item.href}
                  className="flex items-center justify-between py-3 text-sm transition hover:text-emerald-300"
                >
                  <span className="text-zinc-200">{item.label}</span>
                  <span className="font-semibold text-emerald-400">+{item.amount.toLocaleString()}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link href="/rules" className="mt-4 inline-block text-xs text-zinc-500 hover:text-white">
            Full platform math →
          </Link>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
          <h2 className="text-lg font-semibold text-white">Ways to spend</h2>
          <p className="mt-1 text-sm text-zinc-500">Rent agents, trade, and unlock platform features.</p>
          <ul className="mt-4 divide-y divide-zinc-800">
            {WAYS_TO_SPEND.map((item) => (
              <li key={item.label}>
                <Link href={item.href} className="block py-3 transition hover:bg-zinc-900/40">
                  <p className="text-sm font-medium text-zinc-200">{item.label}</p>
                  <p className="text-xs text-zinc-500">{item.detail}</p>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
        <h2 className="text-lg font-semibold text-white">Complete earn rate table</h2>
        <p className="mt-1 text-sm text-zinc-500">
          All configured DDollar awards on the platform ({POINT_ACTIONS.filter((a) => a.amount > 0).length} actions).
        </p>
        <ul className="mt-4 max-h-72 divide-y divide-zinc-800 overflow-y-auto">
          {POINT_ACTIONS.filter((a) => a.amount > 0).map((action) => (
            <li key={action.key} className="flex items-center justify-between py-2 text-sm">
              <span className="text-zinc-300">{action.label}</span>
              <span className="font-semibold text-emerald-400">+{action.amount.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      </section>

      {recentLedger.length > 0 && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
          <h2 className="text-lg font-semibold text-white">Recent activity</h2>
          <ul className="mt-4 divide-y divide-zinc-800">
            {recentLedger.map((entry) => (
              <li key={entry.id} className="flex items-center justify-between py-2.5 text-sm">
                <span className="text-zinc-300">{entry.label || pointActionLabel(entry.actionKey)}</span>
                <span className={entry.amount >= 0 ? 'font-semibold text-emerald-400' : 'font-semibold text-amber-400'}>
                  {entry.amount >= 0 ? '+' : ''}
                  {entry.amount.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
          <Link href="/account?tab=activity" className="mt-4 inline-block text-xs text-zinc-500 hover:text-white">
            Full activity history →
          </Link>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/20 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-white">{value}</p>
    </div>
  );
}

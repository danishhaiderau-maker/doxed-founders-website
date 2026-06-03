'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { DDOLLAR_CURRENCY_NAME, formatDdollar, POINTS, pointActionLabel } from '@dcf/utils';
import {
  AccountOverview,
  AccountPointLedgerEntry,
  fetchAccountOverview,
  fetchAccountPointLedger,
} from '@/lib/api';

const WAYS_TO_EARN = [
  { label: 'Vote on listing', amount: POINTS.LISTING_VOTE, href: '/trust-center?tab=scout-voting' },
  { label: 'Helpful review', amount: POINTS.VALIDATION_HELPFUL, href: '/trust-center?tab=reviews' },
  { label: 'Correct validation', amount: POINTS.VALIDATION_CORRECT, href: '/trust-center?tab=scout-voting' },
  { label: 'Confirmed scam report', amount: POINTS.SCAM_CONFIRMED, href: '/trust-center?tab=investigations' },
  { label: 'Daily login', amount: POINTS.DAILY_LOGIN, href: '/feed' },
  { label: 'Build update', amount: POINTS.FOUNDER_BUILD_POST, href: '/founder-den' },
  { label: 'Project listed', amount: POINTS.LISTING_SCOUT_APPROVED, href: '/list-your-project' },
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

        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          <Stat label="Lifetime earned" value={formatDdollar(stats.lifetimeEarned || overview?.reputation.reputationPoints || 0)} />
          <Stat label="Lifetime spent" value={formatDdollar(stats.lifetimeSpent)} />
          <Stat label="Pending rewards" value={formatDdollar(stats.pendingRewards)} />
        </div>
      </section>

      <div className="grid gap-8 lg:grid-cols-2">
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-5">
          <h2 className="text-lg font-semibold text-white">Ways to earn</h2>
          <p className="mt-1 text-sm text-zinc-500">Participate in trust, builds, and community validation.</p>
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

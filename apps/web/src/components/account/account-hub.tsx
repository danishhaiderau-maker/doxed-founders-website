'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  contributorLevelLabel,
  formatUsd,
  pointsToNextLevel,
} from '@dcf/utils';
import { SecuritySettingsPanel } from '@/components/settings/security-settings-panel';
import { ReputationBadge } from '@/components/landing/project-spotlight';
import { GamifiedRoleBadge, BuilderStatusBadge } from '@/components/account/gamified-role-badge';
import { NotificationSettingsPanel } from '@/components/account/notification-settings-panel';
import { ConnectedAccountsPanel } from '@/components/account/connected-accounts-panel';
import { TopUpPanel } from '@/components/account/topup-panel';
import {
  AccountActivityItem,
  AccountOverview,
  AccountPointLedgerEntry,
  fetchAccountActivity,
  fetchAccountOverview,
  fetchAccountPointLedger,
} from '@/lib/api';

export type AccountTab =
  | 'overview'
  | 'security'
  | 'notifications'
  | 'connected'
  | 'points'
  | 'reputation'
  | 'activity'
  | 'topup';

const TABS: { id: AccountTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'topup', label: 'Top up' },
  { id: 'security', label: 'Security' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'connected', label: 'Connected Accounts' },
  { id: 'points', label: 'Points & Rewards' },
  { id: 'reputation', label: 'Reputation' },
  { id: 'activity', label: 'Activity History' },
];

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function groupLedgerByDate(entries: AccountPointLedgerEntry[]) {
  const groups = new Map<string, AccountPointLedgerEntry[]>();
  for (const entry of entries) {
    const key = new Date(entry.createdAt).toLocaleDateString(undefined, {
      month: 'long',
      day: 'numeric',
    });
    const list = groups.get(key) ?? [];
    list.push(entry);
    groups.set(key, list);
  }
  return groups;
}

export function AccountHub({ initialTab = 'overview' }: { initialTab?: AccountTab }) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [tab, setTab] = useState<AccountTab>(initialTab);
  const [overview, setOverview] = useState<AccountOverview | null>(null);
  const [ledger, setLedger] = useState<AccountPointLedgerEntry[]>([]);
  const [activity, setActivity] = useState<AccountActivityItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [ov, lg, act] = await Promise.all([
        fetchAccountOverview(token),
        fetchAccountPointLedger(token),
        fetchAccountActivity(token),
      ]);
      setOverview(ov);
      setLedger(lg);
      setActivity(act);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load account');
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  const progress = useMemo(
    () => (overview ? pointsToNextLevel(overview.reputation.reputationPoints) : null),
    [overview],
  );

  const ledgerGroups = useMemo(() => groupLedgerByDate(ledger), [ledger]);

  if (!token) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-sm text-amber-100">
        <Link href="/login?callbackUrl=/account" className="font-semibold underline">
          Sign in
        </Link>{' '}
        to manage your account.
      </div>
    );
  }

  if (error && !overview) {
    return <p className="text-sm text-red-300">{error}</p>;
  }

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="lg:w-52 lg:shrink-0">
        <nav className="flex flex-wrap gap-1 lg:flex-col">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`rounded-lg px-3 py-2 text-left text-sm transition ${
                tab === item.id
                  ? 'bg-emerald-500/20 font-semibold text-emerald-100 ring-1 ring-emerald-500/40'
                  : 'text-zinc-400 hover:bg-zinc-900 hover:text-white'
              }`}
            >
              {item.label}
            </button>
          ))}
          <Link
            href="/notifications"
            className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-900 hover:text-white"
          >
            Notification Inbox →
          </Link>
          <Link
            href="/leaderboard"
            className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-900 hover:text-white"
          >
            Public Leaderboard →
          </Link>
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        {tab === 'overview' && overview && (
          <section className="space-y-6">
            {overview.adminBanner && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-950/30 px-4 py-3 text-sm font-medium text-rose-100">
                {overview.adminBanner}
              </div>
            )}

            <div className="flex flex-wrap items-start gap-4 rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-zinc-800 text-2xl font-bold text-white">
                {overview.username.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-bold text-white">{overview.username}</h2>
                  <GamifiedRoleBadge role={overview.gamifiedRole} size="md" />
                  {overview.builderStatus.badge && (
                    <BuilderStatusBadge badge={overview.builderStatus.badge} />
                  )}
                </div>
                <p className="mt-1 text-sm text-zinc-500">{overview.email}</p>
                <p className="mt-2 text-sm text-zinc-400">
                  Joined {formatDate(overview.joinedAt)}
                  {overview.reputation.rank != null && (
                    <> · Leaderboard rank #{overview.reputation.rank}</>
                  )}
                </p>
                {overview.builderStatus.founderSlug && (
                  <Link
                    href={`/founder/${overview.builderStatus.founderSlug}`}
                    className="mt-2 inline-block text-sm text-emerald-400 hover:underline"
                  >
                    View founder profile →
                  </Link>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h3 className="font-semibold text-white">Signed in with</h3>
              <ul className="mt-3 space-y-2">
                {overview.authMethods.length === 0 ? (
                  <li className="text-sm text-zinc-500">No authentication methods on file.</li>
                ) : (
                  overview.authMethods.map((method) => (
                    <li key={method.provider} className="flex items-center gap-2 text-sm text-zinc-300">
                      <span className="text-emerald-400">✓</span>
                      {method.label}
                    </li>
                  ))
                )}
              </ul>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard label="Points" value={overview.reputation.reputationPoints.toLocaleString()} />
              <StatCard
                label="Rank"
                value={contributorLevelLabel(overview.reputation.contributorLevel)}
              />
              <StatCard label="Following" value={String(overview.followingCount)} />
            </div>
          </section>
        )}

        {tab === 'security' && token && <SecuritySettingsPanel accessToken={token} />}

        {tab === 'notifications' && token && (
          <NotificationSettingsPanel accessToken={token} />
        )}

        {tab === 'connected' && token && <ConnectedAccountsPanel accessToken={token} />}

        {tab === 'points' && overview && (
          <section className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <StatCard
                label="Current points"
                value={overview.reputation.reputationPoints.toLocaleString()}
              />
              <StatCard
                label="Leaderboard position"
                value={
                  overview.reputation.rank != null
                    ? `#${overview.reputation.rank} of ${overview.reputation.totalParticipants}`
                    : 'Unranked'
                }
              />
            </div>
            {progress && progress.nextLevel != null && (
              <div className="rounded-xl border border-zinc-800 p-4">
                <p className="text-sm text-zinc-400">
                  {progress.pointsNeeded.toLocaleString()} points to level{' '}
                  {progress.nextLevel} ({contributorLevelLabel(progress.nextLevel)})
                </p>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className="h-full bg-emerald-500 transition-all"
                    style={{
                      width: `${Math.min(
                        100,
                        ((overview.reputation.reputationPoints -
                          (progress.currentLevel === 1
                            ? 0
                            : [0, 250, 800, 2000, 5000][progress.currentLevel - 1] ?? 0)) /
                          Math.max(1, progress.pointsNeeded)) *
                          100,
                      )}%`,
                    }}
                  />
                </div>
              </div>
            )}

            <div>
              <h3 className="font-semibold text-white">Point history</h3>
              <p className="mt-1 text-xs text-zinc-500">
                Every reward is logged so you always know why points were earned.
              </p>
              {ledger.length === 0 ? (
                <p className="mt-4 text-sm text-zinc-500">No point activity yet.</p>
              ) : (
                <div className="mt-4 space-y-6">
                  {[...ledgerGroups.entries()].map(([date, entries]) => (
                    <div key={date}>
                      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                        {date}
                      </p>
                      <ul className="mt-2 divide-y divide-zinc-800 rounded-xl border border-zinc-800">
                        {entries.map((entry) => (
                          <li
                            key={entry.id}
                            className="flex items-center justify-between px-4 py-3"
                          >
                            <div>
                              <p className="text-sm font-medium text-emerald-300">
                                +{entry.amount}
                              </p>
                              <p className="text-sm text-zinc-300">{entry.label}</p>
                            </div>
                            <span className="text-xs text-zinc-600">
                              {new Date(entry.createdAt).toLocaleTimeString(undefined, {
                                hour: 'numeric',
                                minute: '2-digit',
                              })}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {tab === 'reputation' && overview && (
          <section className="space-y-6">
            <div className="flex flex-wrap items-center gap-3">
              <ReputationBadge
                points={overview.reputation.reputationPoints}
                level={overview.reputation.contributorLevel}
              />
              <GamifiedRoleBadge role={overview.gamifiedRole} size="md" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <StatCard
                label="Estimated airdrop share"
                value={`${overview.reputation.airdropPoolPercent.toFixed(4)}% of pool`}
              />
              <StatCard
                label="Estimated value at launch"
                value={formatUsd(overview.reputation.estimatedUsd)}
              />
            </div>
            <p className="text-sm text-zinc-400">
              Reputation is earned through quality contributions — build updates, scout accuracy,
              helpful replies, and conviction trading. Spam does not earn points.
            </p>
            <Link href="/leaderboard" className="text-sm text-emerald-400 hover:underline">
              View public leaderboard →
            </Link>
          </section>
        )}

        {tab === 'topup' && token && (
          <section>
            <TopUpPanel accessToken={token} />
          </section>
        )}

        {tab === 'activity' && (
          <section>
            <h3 className="font-semibold text-white">Recent activity</h3>
            <p className="mt-1 text-xs text-zinc-500">
              Notifications and platform events tied to your account.
            </p>
            {activity.length === 0 ? (
              <p className="mt-4 text-sm text-zinc-500">No activity yet.</p>
            ) : (
              <ul className="mt-4 divide-y divide-zinc-800 rounded-xl border border-zinc-800">
                {activity.map((item) => (
                  <li key={item.id} className="px-4 py-3">
                    <p className="text-sm font-medium text-white">{item.title}</p>
                    <p className="text-sm text-zinc-400">{item.body}</p>
                    <p className="mt-1 text-xs text-zinc-600">{formatDate(item.createdAt)}</p>
                    {item.link && (
                      <Link href={item.link} className="mt-1 inline-block text-xs text-emerald-400">
                        View →
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-xl font-bold text-white">{value}</p>
    </div>
  );
}

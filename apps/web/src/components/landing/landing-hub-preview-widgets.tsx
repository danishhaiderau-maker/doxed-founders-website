'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useMemo, useState } from 'react';
import { POINTS, formatUsd } from '@dcf/utils';
import {
  AppNotification,
  AccountOverview,
  AccountPointLedgerEntry,
  PlatformStats,
  TrustCenterOverview,
  UnifiedFeedItem,
  fetchAccountOverview,
  fetchAccountPointLedger,
  fetchNotifications,
  fetchTrustCenterOverview,
  fetchTrustCommunityReviews,
  fetchUnifiedFeed,
} from '@/lib/api';

const FEED_FILTERS = ['All', 'Projects', 'Announcements', 'Listings', 'Investigations', 'Agent'] as const;

const WAYS_TO_EARN = [
  { label: 'Vote on listing', amount: POINTS.LISTING_VOTE },
  { label: 'Helpful review', amount: POINTS.VALIDATION_HELPFUL },
  { label: 'Correct validation', amount: POINTS.VALIDATION_CORRECT },
  { label: 'Daily login', amount: POINTS.DAILY_LOGIN },
  { label: 'Build update', amount: POINTS.FOUNDER_BUILD_POST },
] as const;

const WAYS_TO_SPEND = [
  'BTC Agent Rental',
  'Paper Trading Top-Ups',
  'Premium Features',
  'Future Platform Services',
] as const;

const FOUNDER_OS_ITEMS = [
  { label: 'Mission Control', href: '/founder-den', icon: '◆' },
  { label: 'Tasks', href: '/founder-den?tab=activity', icon: '☑' },
  { label: 'Agents', href: '/founder-den?tab=agents', icon: '🤖' },
  { label: 'Copilot', href: '/founder-den', icon: '✦' },
  { label: 'Progress', href: '/founder-den', icon: '📈' },
] as const;

const FOUNDER_NODE_ITEMS = [
  { label: 'Self custody', icon: '🔐' },
  { label: 'Encrypted memory', icon: '💾' },
  { label: 'TEE protected', icon: '🔒' },
] as const;

const AI_STACK_ITEMS = ['DeepSeek', 'Claude', 'OpenAI', 'Gemini', 'Cursor'] as const;

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function WidgetShell({
  title,
  subtitle,
  headerClass,
  href,
  footerLabel = 'View all →',
  children,
}: {
  title: string;
  subtitle: string;
  headerClass: string;
  href: string;
  footerLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[22rem] flex-col overflow-hidden rounded-xl border border-zinc-800/90 bg-zinc-950/80">
      <div className={`border-b border-zinc-800/80 px-3 py-2.5 ${headerClass}`}>
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-white">{title}</p>
        <p className="mt-0.5 text-[10px] leading-snug text-zinc-400">{subtitle}</p>
      </div>
      <div className="flex flex-1 flex-col px-3 py-2.5">{children}</div>
      <Link
        href={href}
        className="border-t border-zinc-800/80 px-3 py-2 text-[10px] font-semibold text-zinc-400 transition hover:text-white"
      >
        {footerLabel}
      </Link>
    </div>
  );
}

function FeedRoleBadge({ category }: { category: UnifiedFeedItem['category'] }) {
  const label =
    category === 'founder' ? 'Founder' : category === 'trading' ? 'Agent' : category === 'market' ? 'Market' : 'Admin';
  return (
    <span className="rounded bg-zinc-800 px-1 py-0.5 text-[8px] font-medium uppercase tracking-wide text-zinc-400">
      {label}
    </span>
  );
}

function notificationTone(type: string) {
  if (type.includes('DD') || type.includes('POINT') || type.includes('REWARD')) return 'text-emerald-400 bg-emerald-950/40';
  if (type.includes('VOTE') || type.includes('LISTING')) return 'text-amber-300 bg-amber-950/40';
  if (type.includes('INVEST') || type.includes('SCAM')) return 'text-red-300 bg-red-950/40';
  if (type.includes('AGENT') || type.includes('TRADE')) return 'text-sky-300 bg-sky-950/40';
  return 'text-violet-300 bg-violet-950/40';
}

type HubPreviewData = {
  feed: UnifiedFeedItem[];
  trust: TrustCenterOverview | null;
  reviewCount: number;
  overview: AccountOverview | null;
  ledger: AccountPointLedgerEntry[];
  notifications: AppNotification[];
};

export function LandingHubPreviewWidgets({
  scoutPending,
  platformStats,
}: {
  scoutPending: number;
  platformStats: PlatformStats | null;
}) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [data, setData] = useState<HubPreviewData>({
    feed: [],
    trust: null,
    reviewCount: 0,
    overview: null,
    ledger: [],
    notifications: [],
  });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [feedRes, trust, reviews] = await Promise.all([
          fetchUnifiedFeed('all'),
          fetchTrustCenterOverview(),
          fetchTrustCommunityReviews(),
        ]);
        let overview: AccountOverview | null = null;
        let ledger: AccountPointLedgerEntry[] = [];
        let notifications: AppNotification[] = [];
        if (token) {
          [overview, ledger, notifications] = await Promise.all([
            fetchAccountOverview(token),
            fetchAccountPointLedger(token, 50),
            fetchNotifications(token),
          ]);
        }
        if (!cancelled) {
          setData({
            feed: feedRes.items.slice(0, 4),
            trust,
            reviewCount: reviews.length,
            overview,
            ledger,
            notifications: notifications.slice(0, 5),
          });
        }
      } catch {
        if (!cancelled) setData((prev) => prev);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const ddStats = useMemo(() => {
    const balance = data.overview?.reputation.reputationPoints ?? 0;
    const lifetimeEarned = data.ledger.filter((e) => e.amount > 0).reduce((s, e) => s + e.amount, 0);
    const lifetimeSpent = data.ledger.filter((e) => e.amount < 0).reduce((s, e) => s + Math.abs(e.amount), 0);
    const pendingRewards = data.ledger
      .filter((e) => e.amount > 0 && e.actionKey.includes('PENDING'))
      .reduce((s, e) => s + e.amount, 0);
    return { balance, lifetimeEarned, lifetimeSpent, pendingRewards };
  }, [data.ledger, data.overview]);

  const trustCounts = {
    pending: data.trust?.pendingListings ?? scoutPending,
    reviews: data.reviewCount,
    scout: scoutPending,
    investigations: data.trust?.activeInvestigations ?? platformStats?.activeInvestigations ?? 0,
    delisting: data.trust?.recentlyDelisted ?? 0,
    listed: data.trust?.recentlyListed ?? 0,
  };

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
      <WidgetShell
        title="Feed"
        subtitle="All public updates in one place."
        headerClass="bg-amber-950/30"
        href="/feed"
        footerLabel="View all updates →"
      >
        <div className="mb-2 flex flex-wrap gap-1">
          {FEED_FILTERS.map((f, i) => (
            <span
              key={f}
              className={`rounded-full px-2 py-0.5 text-[9px] font-medium ${
                i === 0 ? 'bg-amber-500/25 text-amber-100' : 'bg-zinc-900 text-zinc-500'
              }`}
            >
              {f}
            </span>
          ))}
        </div>
        <ul className="space-y-2.5">
          {data.feed.length === 0 ? (
            <li className="text-[11px] text-zinc-500">Loading platform activity…</li>
          ) : (
            data.feed.map((item) => (
              <li key={item.id} className="flex gap-2">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs">
                  {item.emoji ?? '📣'}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[11px] font-semibold text-zinc-100">
                    <span>{item.traderName ?? item.headline.split(':')[0]?.trim() ?? 'Platform'}</span>{' '}
                    <FeedRoleBadge category={item.category} />
                  </p>
                  <p className="line-clamp-2 text-[10px] leading-snug text-zinc-400">{item.headline}</p>
                  <p className="mt-0.5 text-[9px] text-zinc-600">{timeAgo(item.at)}</p>
                </div>
              </li>
            ))
          )}
        </ul>
      </WidgetShell>

      <WidgetShell
        title="DDollar"
        subtitle="Earn DDollar by contributing to the ecosystem."
        headerClass="bg-amber-950/30"
        href="/ddollar"
      >
        {token && data.overview ? (
          <>
            <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-black/40 px-2.5 py-2">
              <span className="text-lg" aria-hidden>
                💵
              </span>
              <div>
                <p className="text-[9px] uppercase tracking-wide text-zinc-500">Current balance</p>
                <p className="text-lg font-bold text-white">{ddStats.balance.toLocaleString()} DD</p>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-1.5">
              <MiniStat label="Earned" value={`${(ddStats.lifetimeEarned || ddStats.balance).toLocaleString()}`} tone="text-emerald-400" />
              <MiniStat label="Spent" value={`${ddStats.lifetimeSpent.toLocaleString()}`} tone="text-red-300" />
              <MiniStat label="Pending" value={`${ddStats.pendingRewards.toLocaleString()}`} tone="text-sky-300" />
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-amber-500/20 bg-black/40 px-2.5 py-3">
            <p className="text-lg font-bold text-white">
              {platformStats ? formatUsd(platformStats.simulatedCapital, 0) : '—'} in ecosystem
            </p>
            <p className="mt-1 text-[10px] text-zinc-500">
              <Link href="/login?callbackUrl=/ddollar" className="text-amber-200 underline">
                Sign in
              </Link>{' '}
              to see your wallet
            </p>
          </div>
        )}
        <div className="mt-3 grid flex-1 grid-cols-2 gap-2 text-[10px]">
          <div>
            <p className="mb-1 font-semibold uppercase tracking-wide text-zinc-500">Ways to earn</p>
            <ul className="space-y-1 text-zinc-400">
              {WAYS_TO_EARN.map((w) => (
                <li key={w.label} className="flex justify-between gap-1">
                  <span className="truncate">{w.label}</span>
                  <span className="shrink-0 text-emerald-400">+{w.amount}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-1 font-semibold uppercase tracking-wide text-zinc-500">Ways to spend</p>
            <ul className="space-y-1 text-zinc-400">
              {WAYS_TO_SPEND.map((w) => (
                <li key={w} className="truncate">
                  {w}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </WidgetShell>

      <WidgetShell
        title="Trust Center"
        subtitle="Community validation & safety."
        headerClass="bg-zinc-900/80"
        href="/trust-center"
      >
        <ul className="space-y-1.5">
          <TrustRow href="/trust-center?tab=scout-voting" label="Pending Listings" count={trustCounts.pending} tone="text-amber-300" />
          <TrustRow href="/trust-center?tab=reviews" label="Community Reviews" count={trustCounts.reviews} tone="text-sky-300" />
          <TrustRow href="/trust-center?tab=scout-voting" label="Scout Voting" count={trustCounts.scout} tone="text-emerald-300" />
          <TrustRow href="/trust-center?tab=investigations" label="Investigations" count={trustCounts.investigations} tone="text-red-300" />
          <TrustRow href="/trust-center?tab=delisted" label="Delisting Requests" count={trustCounts.delisting} tone="text-orange-300" />
        </ul>
        <div className="mt-3 space-y-1 border-t border-zinc-800/80 pt-2 text-[10px]">
          <Link href="/trust-center?tab=listed" className="flex items-center justify-between text-zinc-400 hover:text-white">
            <span>Recently Listed</span>
            <span className="text-emerald-400">{trustCounts.listed} active</span>
          </Link>
          <Link href="/trust-center?tab=delisted" className="flex items-center justify-between text-zinc-400 hover:text-white">
            <span>Recently Delisted</span>
            <span className="text-red-300">{trustCounts.delisting} removed</span>
          </Link>
        </div>
      </WidgetShell>

      <WidgetShell
        title="Founder OS"
        subtitle="The cockpit — mission control for builders."
        headerClass="bg-violet-950/40"
        href="/founder-den"
        footerLabel="Open Mission Control →"
      >
        <ul className="space-y-0.5">
          {FOUNDER_OS_ITEMS.map((tool) => (
            <li key={tool.label}>
              <Link
                href={tool.href}
                className="flex items-center gap-2 rounded-lg px-1.5 py-1.5 text-[11px] text-zinc-300 transition hover:bg-violet-950/30 hover:text-white"
              >
                <span className="flex h-6 w-6 items-center justify-center rounded-md border border-zinc-800 bg-zinc-900 text-xs">
                  {tool.icon}
                </span>
                {tool.label}
              </Link>
            </li>
          ))}
        </ul>
      </WidgetShell>

      <WidgetShell
        title="Founder Node"
        subtitle="The vault — your data stays yours."
        headerClass="bg-emerald-950/35"
        href="/settings/builder"
        footerLabel="Download Founder Node →"
      >
        <ul className="space-y-1.5">
          {FOUNDER_NODE_ITEMS.map((item) => (
            <li
              key={item.label}
              className="flex items-center gap-2 rounded-lg border border-emerald-500/15 bg-black/30 px-2 py-1.5 text-[11px] text-zinc-300"
            >
              <span aria-hidden>{item.icon}</span>
              {item.label}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">
          Phala TEE · local vault · metadata sync only
        </p>
      </WidgetShell>

      <WidgetShell
        title="AI Stack"
        subtitle="The brain — bring your own keys."
        headerClass="bg-sky-950/30"
        href="/settings/builder"
        footerLabel="Connect providers →"
      >
        <p className="mb-2 text-[10px] font-medium text-sky-200/90">Use the AI you trust</p>
        <div className="flex flex-wrap gap-1.5">
          {AI_STACK_ITEMS.map((name) => (
            <span
              key={name}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300"
            >
              {name}
            </span>
          ))}
        </div>
        <p className="mt-3 text-[10px] leading-relaxed text-zinc-500">
          Your key. Your model. Founder OS orchestrates agents, memory, and execution.
        </p>
      </WidgetShell>

      <WidgetShell
        title="Notifications"
        subtitle="Your personal activity."
        headerClass="bg-emerald-950/30"
        href="/notifications"
      >
        {!token ? (
          <p className="text-[11px] text-zinc-500">
            <Link href="/login?callbackUrl=/notifications" className="text-emerald-300 underline">
              Sign in
            </Link>{' '}
            to see votes, DDollar rewards, and agent alerts.
          </p>
        ) : data.notifications.length === 0 ? (
          <p className="text-[11px] text-zinc-500">No notifications yet — scout, trade, or follow founders to get updates.</p>
        ) : (
          <ul className="space-y-2">
            {data.notifications.map((n) => (
              <li key={n.id} className="flex gap-2">
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] ${notificationTone(n.type)}`}
                  aria-hidden
                >
                  •
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[11px] font-medium text-zinc-200">{n.title}</p>
                  <p className="line-clamp-2 text-[10px] text-zinc-500">{n.body}</p>
                  <p className="mt-0.5 text-[9px] text-zinc-600">{timeAgo(n.createdAt)}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </WidgetShell>
      </div>
    </div>
  );
}

function MiniStat({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-black/30 px-1.5 py-1.5 text-center">
      <p className={`text-[11px] font-bold ${tone}`}>{value}</p>
      <p className="text-[8px] uppercase tracking-wide text-zinc-600">{label}</p>
    </div>
  );
}

function TrustRow({
  href,
  label,
  count,
  tone,
}: {
  href: string;
  label: string;
  count: number;
  tone: string;
}) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between rounded-lg border border-zinc-800/60 bg-black/20 px-2.5 py-2 transition hover:border-zinc-600 hover:bg-zinc-900/50"
    >
      <span className="text-[11px] text-zinc-300">{label}</span>
      <span className={`text-sm font-bold ${tone}`}>
        {count}
        <span className="ml-1 text-[10px] text-zinc-600">›</span>
      </span>
    </Link>
  );
}

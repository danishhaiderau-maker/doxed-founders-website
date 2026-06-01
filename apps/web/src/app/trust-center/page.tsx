'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { contributorLevelLabel, trustWeightLabel, VALIDATION_LABELS } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import {
  ScoutListing,
  TrustCenterOverview,
  TrustInvestigation,
  castScoutVote,
  fetchOpenScoutListings,
  fetchTrustCenterOverview,
  fetchTrustInvestigations,
  fetchTrustRecentlyDelisted,
  fetchTrustRecentlyListed,
} from '@/lib/api';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'pending', label: 'Pending Listings' },
  { id: 'investigations', label: 'Investigations' },
  { id: 'listed', label: 'Recently Listed' },
  { id: 'delisted', label: 'Recently Delisted' },
  { id: 'scouts', label: 'Top Scouts' },
] as const;

type TabId = (typeof TABS)[number]['id'];

const VALIDATION_OPTIONS = Object.entries(VALIDATION_LABELS) as [
  keyof typeof VALIDATION_LABELS,
  string,
][];

function TrustCenterInner() {
  const { data: session } = useSession();
  const searchParams = useSearchParams();
  const initialTab = (searchParams.get('tab') as TabId) || 'overview';
  const [tab, setTab] = useState<TabId>(initialTab);
  const [overview, setOverview] = useState<TrustCenterOverview | null>(null);
  const [pending, setPending] = useState<ScoutListing[]>([]);
  const [investigations, setInvestigations] = useState<TrustInvestigation[]>([]);
  const [listed, setListed] = useState<Awaited<ReturnType<typeof fetchTrustRecentlyListed>>>([]);
  const [delisted, setDelisted] = useState<Awaited<ReturnType<typeof fetchTrustRecentlyDelisted>>>([]);
  const [error, setError] = useState<string | null>(null);
  const [voteBusy, setVoteBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [ov, open, inv, recent, removed] = await Promise.all([
      fetchTrustCenterOverview(),
      fetchOpenScoutListings(),
      fetchTrustInvestigations(),
      fetchTrustRecentlyListed(),
      fetchTrustRecentlyDelisted(),
    ]);
    setOverview(ov);
    setPending(open);
    setInvestigations(inv);
    setListed(recent);
    setDelisted(removed);
  }, []);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : 'Failed to load Trust Center'));
  }, [load]);

  async function handleValidationVote(
    listingId: string,
    category: keyof typeof VALIDATION_LABELS,
    comment: string,
  ) {
    if (!session?.accessToken) {
      setError('Sign in to validate listings');
      return;
    }
    setVoteBusy(listingId);
    setError(null);
    try {
      await castScoutVote(listingId, { validationCategory: category, comment }, session.accessToken);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Vote failed');
    } finally {
      setVoteBusy(null);
    }
  }

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Trust Center</h1>
            <p className="mt-1 max-w-xl text-sm text-zinc-400">
              The community protects the network. Doxxed evaluates founder credibility — not token price.
              DDollar rewards accuracy. Trust weight is earned, capped at 10 — not purchased.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-6">
        <div className="mb-6 flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`rounded-lg px-3 py-1.5 text-sm ${
                tab === t.id
                  ? 'bg-emerald-600 text-white'
                  : 'border border-zinc-700 text-zinc-400 hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
          <Link
            href="/list-your-project"
            className="ml-auto rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-semibold hover:bg-violet-500"
          >
            List project →
          </Link>
        </div>

        {error && (
          <p className="mb-4 rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        {tab === 'overview' && overview && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: 'Pending listings', value: overview.pendingListings, href: '?tab=pending' },
              { label: 'Active investigations', value: overview.activeInvestigations, href: '?tab=investigations' },
              { label: 'Listed (14d)', value: overview.recentlyListed, href: '?tab=listed' },
              { label: 'Delisted (14d)', value: overview.recentlyDelisted, href: '?tab=delisted' },
            ].map((card) => (
              <Link
                key={card.label}
                href={`/trust-center${card.href}`}
                className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-4 hover:border-emerald-500/40"
              >
                <p className="text-3xl font-bold">{card.value}</p>
                <p className="text-sm text-zinc-500">{card.label}</p>
              </Link>
            ))}
            <div className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-4 sm:col-span-2 lg:col-span-4">
              <p className="text-xs uppercase tracking-wider text-zinc-500">Thresholds</p>
              <p className="mt-2 text-sm text-zinc-300">
                Listing: {overview.thresholds.listingApprovalPercent}% weighted approval ·{' '}
                {overview.platformStats.requiredVoters} voters · {overview.thresholds.windowHours}h window
              </p>
              <p className="mt-1 text-sm text-zinc-300">
                Investigation: {overview.thresholds.investigationScamPercent}% scam signal → admin review (no instant
                delist)
              </p>
              <p className="mt-3 text-xs text-zinc-500">
                DDollar is not withdrawable. No intrinsic value. Ecosystem currency for participation only.
              </p>
            </div>
          </div>
        )}

        {tab === 'pending' && (
          <div className="space-y-4">
            {pending.length === 0 ? (
              <p className="text-zinc-500">No listings awaiting community validation.</p>
            ) : (
              pending.map((listing) => (
                <article key={listing.id} className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-bold">{listing.projectName}</h2>
                      <p className="text-sm text-zinc-500">${listing.ticker}</p>
                      <p className="mt-2 text-sm text-zinc-400">
                        {listing.tally.yesPercent}% trusted · {listing.tally.total}/{listing.requiredVoters} voters ·{' '}
                        {listing.tally.remainingVoters} needed
                      </p>
                    </div>
                    <Link href={`/scout-votes`} className="text-xs text-sky-400 hover:underline">
                      Full scout board →
                    </Link>
                  </div>
                  {session && (
                    <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {VALIDATION_OPTIONS.map(([key, label]) => (
                        <button
                          key={key}
                          type="button"
                          disabled={voteBusy === listing.id}
                          onClick={() => {
                            const comment = window.prompt(`Optional review for "${label}" (20+ chars recommended):`) ?? '';
                            void handleValidationVote(listing.id, key, comment);
                          }}
                          className="rounded-lg border border-zinc-700 px-3 py-2 text-left text-xs hover:border-emerald-500/50"
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))
            )}
          </div>
        )}

        {tab === 'investigations' && (
          <div className="space-y-4">
            {investigations.length === 0 ? (
              <p className="text-zinc-500">No active community investigations.</p>
            ) : (
              investigations.map((inv) => (
                <article key={inv.id} className="rounded-2xl border border-amber-500/30 bg-amber-950/10 p-4">
                  <div className="flex flex-wrap justify-between gap-2">
                    <div>
                      <h2 className="font-bold">{inv.project.name}</h2>
                      <p className="text-sm text-zinc-400">{inv.reason ?? 'Community investigation'}</p>
                    </div>
                    <span className="rounded-full border border-amber-500/40 px-2 py-0.5 text-xs text-amber-200">
                      {inv.status.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <p className="mt-2 text-sm">
                    Trust {inv.trustScore}% · Suspicious {inv.scamScore}% · closes{' '}
                    {new Date(inv.closesAt).toLocaleDateString()}
                  </p>
                  <Link href={`/project/${inv.project.slug}`} className="mt-2 inline-block text-xs text-emerald-400">
                    View project →
                  </Link>
                </article>
              ))
            )}
          </div>
        )}

        {tab === 'listed' && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {listed.map((p) => (
              <Link
                key={p.id}
                href={`/project/${p.slug}`}
                className="rounded-xl border border-zinc-800 p-3 hover:border-emerald-500/40"
              >
                <p className="font-semibold">{p.name}</p>
                <p className="text-xs text-zinc-500">${p.ticker}</p>
              </Link>
            ))}
          </div>
        )}

        {tab === 'delisted' && (
          <div className="space-y-3">
            {delisted.map((item) => (
              <div key={item.id} className="rounded-xl border border-zinc-800 p-3">
                <p className="font-semibold">{item.projectName}</p>
                <p className="text-xs text-zinc-500">{item.reviewNotes}</p>
              </div>
            ))}
          </div>
        )}

        {tab === 'scouts' && overview && (
          <div className="space-y-2">
            {overview.topScouts.map((scout, i) => (
              <div
                key={scout.id}
                className="flex items-center justify-between rounded-xl border border-zinc-800 px-4 py-3"
              >
                <div>
                  <span className="text-zinc-500">#{i + 1}</span>{' '}
                  <span className="font-medium">{scout.name ?? 'Scout'}</span>
                  <span className="ml-2 text-xs text-zinc-500">
                    {contributorLevelLabel(scout.contributorLevel)} · weight {scout.trustWeight} (
                    {trustWeightLabel(scout.trustWeight)})
                  </span>
                </div>
                <span className="text-sm text-emerald-300">{scout.reputationPoints.toLocaleString()} DDollar</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}

export default function TrustCenterPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508]" />}>
      <TrustCenterInner />
    </Suspense>
  );
}

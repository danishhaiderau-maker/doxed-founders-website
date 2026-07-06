'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  fetchRaiseRoomDashboard,
  fetchRaiseRoomProjects,
  type RaiseRoomDashboard,
  type RaiseRoomFilter,
  type RaiseRoomProjectCard,
} from '@/lib/api';
import { RaiseRoomHeroDashboard } from './raise-room-hero-dashboard';
import { RaiseRoomDiscoveryFeed, RaiseRoomTrendingCards } from './raise-room-discovery-feed';
import { RaiseRoomSidebar } from './raise-room-sidebar';

export function RaiseRoomDiscoveryHub() {
  const [dashboard, setDashboard] = useState<RaiseRoomDashboard | null>(null);
  const [projects, setProjects] = useState<RaiseRoomProjectCard[]>([]);
  const [filter, setFilter] = useState<RaiseRoomFilter>('trending');
  const [total, setTotal] = useState(0);
  const [loadingDash, setLoadingDash] = useState(true);
  const [loadingFeed, setLoadingFeed] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoadingDash(true);
    try {
      const data = await fetchRaiseRoomDashboard();
      setDashboard(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load Raise Room');
    } finally {
      setLoadingDash(false);
    }
  }, []);

  const loadProjects = useCallback(async (f: RaiseRoomFilter) => {
    setLoadingFeed(true);
    try {
      const data = await fetchRaiseRoomProjects(f);
      setProjects(data.projects);
      setTotal(data.total);
      if (data.hasData) setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load projects');
      setProjects([]);
    } finally {
      setLoadingFeed(false);
    }
  }, []);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    loadProjects(filter);
  }, [filter, loadProjects]);

  const showEmptyHint =
    !loadingDash && !loadingFeed && dashboard && !dashboard.hasData && projects.length === 0;

  return (
    <div className="space-y-8">
      {loadingDash && !dashboard && (
        <div className="rounded-2xl border border-zinc-800 p-10 text-center text-sm text-zinc-500">
          Loading discovery hub…
        </div>
      )}

      {dashboard && (
        <RaiseRoomHeroDashboard stats={dashboard.stats} demoMode={dashboard.demoMode} />
      )}

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {showEmptyHint && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6 text-center">
          <p className="text-zinc-300">Demo ecosystem not seeded yet.</p>
          {dashboard?.demoMode ? (
            <p className="mt-2 text-sm text-zinc-500">
              Run demo seed from{' '}
              <Link href="/admin/demo" className="text-amber-400 hover:underline">
                Admin → Demo
              </Link>{' '}
              (xlarge recommended) then refresh.
            </p>
          ) : (
            <p className="mt-2 text-sm text-zinc-500">
              Founders open Proof Raises after launch qualification — or enable demo mode for smoke data.
            </p>
          )}
        </div>
      )}

      {dashboard && dashboard.trending.length > 0 && (
        <RaiseRoomTrendingCards projects={dashboard.trending} />
      )}

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <RaiseRoomDiscoveryFeed
          filter={filter}
          onFilter={setFilter}
          projects={projects}
          total={total}
          loading={loadingFeed}
        />
        {dashboard && <RaiseRoomSidebar dashboard={dashboard} />}
      </div>
    </div>
  );
}

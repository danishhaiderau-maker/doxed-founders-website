'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { FounderOsDashboardLayout } from '@/components/founder-os-dashboard';
import { FounderDashboard, ProjectRoom } from '@/lib/api';
import type { WorkspaceTab } from '@/components/founder-workspace';

export type FounderMissionControlProps = {
  session: { accessToken: string } | null;
  hasFounder: boolean;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
  tabContent?: ReactNode;
};

export function FounderMissionControl({
  session,
  hasFounder,
  dashboard,
  room,
  activeTab,
  onTabChange,
  onRefresh,
  onMessage,
  tabContent,
}: FounderMissionControlProps) {
  if (!session) {
    return (
      <section className="rounded-2xl border border-dashed border-zinc-700 bg-zinc-900/30 p-10 text-center">
        <p className="text-lg font-semibold text-white">Founder OS Mission Control</p>
        <p className="mt-2 text-sm text-zinc-400">
          Your current project, build queue, copilot memory, and raise room — one screen.
        </p>
        <Link
          href="/login?callbackUrl=/founder-den"
          className="mt-6 inline-block rounded-xl bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-zinc-100"
        >
          Sign in to open Founder OS
        </Link>
      </section>
    );
  }

  if (!hasFounder) {
    return (
      <section className="rounded-2xl border border-amber-500/30 bg-amber-950/15 p-8">
        <p className="text-xs font-semibold uppercase tracking-widest text-amber-400">Activate</p>
        <h2 className="mt-2 text-xl font-bold text-white">Start your founder profile</h2>
        <p className="mt-2 max-w-lg text-sm text-amber-100/80">
          Mission control unlocks after you activate — then you get project memory, GitHub sync, Raise
          Room, and Copilot resume work.
        </p>
        <button
          type="button"
          onClick={() => onTabChange('analytics')}
          className="mt-5 rounded-xl bg-amber-600 px-5 py-2.5 text-sm font-semibold text-black hover:bg-amber-500"
        >
          Activate founder profile →
        </button>
      </section>
    );
  }

  return (
    <FounderOsDashboardLayout
      accessToken={session.accessToken}
      dashboard={dashboard}
      room={room}
      activeTab={activeTab}
      onTabChange={onTabChange}
      onRefresh={onRefresh}
      onMessage={onMessage}
      tabContent={tabContent}
    />
  );
}

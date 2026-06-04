'use client';

import Link from 'next/link';
import { formatDdollar, LIFECYCLE_STAGES } from '@dcf/utils';
import { FounderJourneyProgress } from '@/components/founder-journey-progress';
import { FounderAgentsWorkforce } from '@/components/founder-agents-workforce';
import { FounderSocialHub } from '@/components/founder-social-hub';
import { FounderMissionControl } from '@/components/founder-mission-control';
import { BuilderSettingsPanel } from '@/components/settings/builder-settings-panel';
import { DiscoverMyVisibilityPanel } from '@/components/discover/discover-my-visibility-panel';
import {
  FounderDashboard,
  ProjectRoom,
  fetchBuildRoom,
} from '@/lib/api';
import { useCallback, useEffect, useState } from 'react';
import type { BuildRoomData } from '@/lib/api';

const FOUNDER_STAGES = ['IDEA', 'BRAINSTORMING', 'PROTOTYPE', 'MVP', 'BETA', 'DEMAND_VALIDATION'];

export type WorkspaceTab =
  | 'activity'
  | 'social'
  | 'agents'
  | 'analytics'
  | 'community'
  | 'build'
  | 'launch'
  | 'tasks'
  | 'funding'
  | 'notifications';

const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: 'activity', label: 'Mission Control' },
  { id: 'social', label: 'Social Hub' },
  { id: 'agents', label: 'Agents' },
  { id: 'analytics', label: 'Settings' },
];

export type FounderWorkspaceProps = {
  tab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  session: { accessToken: string } | null;
  hasFounder: boolean;
  currentStage: string;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  onRefresh: () => void;
  onWorkspaceMessage?: (msg: string) => void;
  appForm: {
    projectName: string;
    websiteUrl: string;
    twitterHandle: string;
    githubUrl: string;
    videoUrl: string;
    ideaDescription: string;
    lifecycleStage: string;
  };
  setAppForm: React.Dispatch<React.SetStateAction<FounderWorkspaceProps['appForm']>>;
  buildForm: { headline: string; body: string; dayNumber: string };
  setBuildForm: React.Dispatch<React.SetStateAction<FounderWorkspaceProps['buildForm']>>;
  raiseForm: {
    goalUsd: string;
    durationDays: string;
    tokenAllocation: string;
    communityTokenPercent: string;
    maxParticipantSlots: string;
    plannedLaunchDate: string;
  };
  setRaiseForm: React.Dispatch<React.SetStateAction<FounderWorkspaceProps['raiseForm']>>;
  onSubmitApplication: () => void;
  onPostBuildUpdate: () => void;
  onLaunchRaise: () => void;
  initialCopilotPrompt?: string | null;
  onInitialCopilotPromptConsumed?: () => void;
  activeAgentTemplate?: string | null;
};

function OsSection({
  title,
  subtitle,
  disabled,
  children,
}: {
  title: string;
  subtitle: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5 ${disabled ? 'opacity-60' : ''}`}
    >
      <h3 className="font-semibold text-white">{title}</h3>
      <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
      <div className="mt-4">{children}</div>
      {disabled && (
        <p className="mt-3 text-xs text-amber-400/90">Activate your founder profile to use this section.</p>
      )}
    </section>
  );
}

export function FounderWorkspace(props: FounderWorkspaceProps) {
  const {
    tab,
    onTabChange,
    session,
    hasFounder,
    currentStage,
    dashboard,
    room,
    onRefresh,
    onWorkspaceMessage,
    appForm,
    setAppForm,
    onSubmitApplication,
    initialCopilotPrompt,
    onInitialCopilotPromptConsumed,
    activeAgentTemplate,
  } = props;

  const useDashboardShell = Boolean(session && hasFounder);
  const [buildRoom, setBuildRoom] = useState<BuildRoomData | null>(null);

  const loadBuildRoom = useCallback(async () => {
    if (!session?.accessToken || !hasFounder) return;
    try {
      setBuildRoom(await fetchBuildRoom(session.accessToken));
    } catch {
      setBuildRoom(null);
    }
  }, [session?.accessToken, hasFounder]);

  useEffect(() => {
    void loadBuildRoom();
  }, [loadBuildRoom]);

  const tabPanels = (
    <>
      {tab === 'social' && session && (
        <FounderSocialHub
          accessToken={session.accessToken}
          room={room}
          buildRoom={buildRoom}
          onRefresh={() => {
            void loadBuildRoom();
            onRefresh();
          }}
          onMessage={onWorkspaceMessage}
        />
      )}

      {tab === 'agents' && session && (
        <FounderAgentsWorkforce
          accessToken={session.accessToken}
          room={room}
          buildRoom={buildRoom}
          onTabChange={() => onTabChange('activity')}
        />
      )}

      {tab === 'agents' && !session && (
        <section className="rounded-2xl border border-dashed border-purple-500/40 bg-purple-950/10 p-8 text-center">
          <p className="text-sm text-zinc-400">
            <Link href="/login?callbackUrl=/founder-den?tab=agents" className="text-purple-300 underline">
              Sign in
            </Link>{' '}
            to view your agent workforce.
          </p>
        </section>
      )}

      {tab === 'analytics' && session && (
        <div className="mx-auto max-w-3xl space-y-6">
          {!hasFounder && (
            <OsSection title="Activate founder profile" subtitle="Unlock Mission Control and agents">
              <input
                value={appForm.projectName}
                onChange={(e) => setAppForm({ ...appForm, projectName: e.target.value })}
                placeholder="Project name"
                className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
              <textarea
                value={appForm.ideaDescription}
                onChange={(e) => setAppForm({ ...appForm, ideaDescription: e.target.value })}
                placeholder="What are you building?"
                rows={3}
                className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
              <select
                value={appForm.lifecycleStage}
                onChange={(e) => setAppForm({ ...appForm, lifecycleStage: e.target.value })}
                className="mb-4 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              >
                {FOUNDER_STAGES.map((s) => (
                  <option key={s} value={s}>
                    {LIFECYCLE_STAGES.find((x) => x.key === s)?.label ?? s}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={onSubmitApplication}
                className="w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-semibold text-white"
              >
                Activate founder profile
              </button>
            </OsSection>
          )}

          {hasFounder && (
            <>
              <header>
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
                  Settings
                </p>
                <h1 className="mt-1 text-2xl font-bold text-white">Integrations & AI stack</h1>
                <p className="mt-1 text-sm text-zinc-500">
                  GitHub, Cursor, DeepSeek, OpenHands, Founder Node — all configuration lives here.
                </p>
              </header>
              <BuilderSettingsPanel accessToken={session.accessToken} />
              <DiscoverMyVisibilityPanel />
              {dashboard && (
                <OsSection title="Account stats" subtitle="Ddollar and readiness">
                  <dl className="grid gap-2 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-zinc-500">Followers</dt>
                      <dd className="text-white">{dashboard.followers ?? 0}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-zinc-500">Founder Ddollar</dt>
                      <dd className="text-emerald-300">
                        {formatDdollar(dashboard.founderCredits ?? 0, 0)}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-zinc-500">Launch readiness</dt>
                      <dd className="text-white">
                        {room?.launchReadiness ?? dashboard.launchReadiness ?? 0}%
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-zinc-500">Paper trading Ddollar</dt>
                      <dd className="text-white">{formatDdollar(dashboard.cashBalance, 0)}</dd>
                    </div>
                  </dl>
                </OsSection>
              )}
            </>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className={useDashboardShell ? '' : 'space-y-6'}>
      {!useDashboardShell && (
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white">Founder OS</h2>
            <p className="text-sm text-zinc-500">Mission control · build in public · ship</p>
          </div>
          {session && (
            <Link
              href="/settings/security"
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
            >
              Security →
            </Link>
          )}
        </div>
      )}

      {!useDashboardShell && tab !== 'activity' && (
        <FounderJourneyProgress
          currentStage={currentStage}
          label={hasFounder ? 'Project stage' : 'The founder journey'}
        />
      )}

      {!useDashboardShell && (
        <nav className="flex flex-wrap gap-1 rounded-xl border border-zinc-800 bg-zinc-900/50 p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onTabChange(t.id)}
              className={`rounded-lg px-3 py-2 text-xs font-medium transition sm:text-sm ${
                tab === t.id
                  ? 'bg-emerald-600 text-white'
                  : 'text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}

      {!session && !useDashboardShell && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
          <Link href="/login?callbackUrl=/founder-den" className="font-semibold underline">
            Sign in
          </Link>{' '}
          to open Founder OS.
        </div>
      )}

      {session && !hasFounder && !useDashboardShell && tab !== 'activity' && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
          Activate your founder profile in Settings to unlock Mission Control.
        </div>
      )}

      {(useDashboardShell || tab === 'activity') && (
        <FounderMissionControl
          session={session}
          hasFounder={hasFounder}
          dashboard={dashboard}
          room={room}
          activeTab={tab}
          onTabChange={onTabChange}
          onRefresh={onRefresh}
          onMessage={onWorkspaceMessage}
          tabContent={
            useDashboardShell && tab !== 'activity' ? tabPanels : undefined
          }
          initialCopilotPrompt={initialCopilotPrompt}
          onInitialCopilotPromptConsumed={onInitialCopilotPromptConsumed}
          activeAgentTemplate={activeAgentTemplate}
        />
      )}

      {!useDashboardShell && tab !== 'activity' && tabPanels}
    </div>
  );
}

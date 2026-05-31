'use client';

import Link from 'next/link';
import { formatUsd, LIFECYCLE_STAGES } from '@dcf/utils';
import { FounderJourneyProgress } from '@/components/founder-journey-progress';
import { RaiseRoomPanel } from '@/components/raise-room-panel';
import { BuildRoom2 } from '@/components/build-room-2';
import { AgentsWorkspacePanel } from '@/components/agents-workspace-panel';
import { FounderMissionControl } from '@/components/founder-mission-control';
import {
  FounderDashboard,
  ProjectRoom,
} from '@/lib/api';

const FOUNDER_STAGES = ['IDEA', 'BRAINSTORMING', 'PROTOTYPE', 'MVP', 'BETA', 'DEMAND_VALIDATION'];

export type WorkspaceTab =
  | 'activity'
  | 'tasks'
  | 'community'
  | 'funding'
  | 'agents'
  | 'build'
  | 'analytics';

const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: 'activity', label: 'Mission control' },
  { id: 'build', label: 'Founder Copilot' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'community', label: 'Community' },
  { id: 'funding', label: 'Raise Room' },
  { id: 'agents', label: 'Agents' },
  { id: 'analytics', label: 'Settings' },
];

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
};

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
    buildForm,
    setBuildForm,
    raiseForm,
    setRaiseForm,
    onSubmitApplication,
    onPostBuildUpdate,
    onLaunchRaise,
  } = props;

  const showDashboardChrome = tab === 'activity' && session && hasFounder;

  return (
    <div className="space-y-6">
      {!showDashboardChrome && (
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-white">Founder OS</h2>
          <p className="text-sm text-zinc-500">Mission control · build · validate · launch</p>
        </div>
        {session && (
          <Link
            href="/settings/security"
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
          >
            Security settings →
          </Link>
        )}
      </div>
      )}

      {tab !== 'activity' && (
        <FounderJourneyProgress
          currentStage={currentStage}
          label={hasFounder ? 'Project stage' : 'The founder journey — start anywhere'}
        />
      )}

      {!(tab === 'activity' && session && hasFounder) && (
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

      {!session && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
          <Link href="/login?callbackUrl=/founder-den" className="font-semibold underline">
            Sign in
          </Link>{' '}
          to save your workspace and run simulated raises.
        </div>
      )}

      {session && !hasFounder && tab !== 'activity' && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
          Activate your founder profile in Settings to unlock GitHub,
          Founder Copilot, and integrations.
        </div>
      )}

      {tab === 'activity' && (
        <FounderMissionControl
          session={session}
          hasFounder={hasFounder}
          dashboard={dashboard}
          room={room}
          activeTab={tab}
          onTabChange={onTabChange}
          onRefresh={onRefresh}
          onMessage={onWorkspaceMessage}
        />
      )}

      {tab !== 'activity' && (
        <>
      {tab === 'tasks' && session && (
        <OsSection title="Build queue tasks" subtitle="Quick Build · agents · command bar · bounties" disabled={!hasFounder}>
          <p className="text-sm text-zinc-400">
            Open{' '}
            <button type="button" onClick={() => onTabChange('build')} className="text-emerald-400 underline">
              Founder Copilot
            </button>{' '}
            to manage ideas, tasks, and GitHub issues. Capture ideas on mobile with Quick Build before you forget.
          </p>
          {room?.openBounties && room.openBounties.length > 0 && (
            <ul className="mt-4 space-y-2">
              {room.openBounties.map((b) => (
                <li key={b.id} className="rounded-lg border border-zinc-800 px-3 py-2 text-sm">
                  <span className="font-medium text-white">{b.title}</span>
                  <span className="text-zinc-500"> — {b.rewardCredits} credits</span>
                </li>
              ))}
            </ul>
          )}
          <Link href="/feed" className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
            View build feed →
          </Link>
        </OsSection>
      )}

      {tab === 'community' && (
        <OsSection title="Community & build log" subtitle="Updates · discussion · project room" disabled={!hasFounder}>
          <input
            value={buildForm.headline}
            onChange={(e) => setBuildForm({ ...buildForm, headline: e.target.value })}
            placeholder="Update headline"
            disabled={!hasFounder}
            className="mb-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
          />
          <textarea
            value={buildForm.body}
            onChange={(e) => setBuildForm({ ...buildForm, body: e.target.value })}
            placeholder="What did you ship?"
            rows={3}
            disabled={!hasFounder}
            className="mb-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
          />
          <button
            type="button"
            onClick={onPostBuildUpdate}
            disabled={!hasFounder}
            className="w-full rounded-lg border border-zinc-600 py-2 text-sm disabled:opacity-40"
          >
            Publish build update
          </button>
          {room && (
            <Link
              href={`/project/${room.slug}`}
              className="mt-4 inline-block text-sm text-emerald-400 hover:underline"
            >
              Open project room (channels & demand) →
            </Link>
          )}
        </OsSection>
      )}

      {tab === 'funding' && (
        <OsSection title="Raise Room" subtitle="Public ICO slots · paper dollar demand · token distribution" disabled={!hasFounder}>
          {room?.activeRaise ? (
            <RaiseRoomPanel
              room={room}
              accessToken={session?.accessToken}
              allocAmount="500"
              onAllocAmountChange={() => {}}
              onAllocate={() => {}}
              onMessage={onWorkspaceMessage}
              onRefresh={onRefresh}
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                value={raiseForm.goalUsd}
                onChange={(e) => setRaiseForm({ ...raiseForm, goalUsd: e.target.value })}
                placeholder="Raise target ($)"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={raiseForm.durationDays}
                onChange={(e) => setRaiseForm({ ...raiseForm, durationDays: e.target.value })}
                placeholder="Days open"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={raiseForm.tokenAllocation}
                onChange={(e) => setRaiseForm({ ...raiseForm, tokenAllocation: e.target.value })}
                placeholder="Token allocation label (e.g. 15% public sale)"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={raiseForm.communityTokenPercent}
                onChange={(e) => setRaiseForm({ ...raiseForm, communityTokenPercent: e.target.value })}
                placeholder="Community token % for Raise Room"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={raiseForm.maxParticipantSlots}
                onChange={(e) => setRaiseForm({ ...raiseForm, maxParticipantSlots: e.target.value })}
                placeholder="Max ICO slots (optional)"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <button
                type="button"
                onClick={onLaunchRaise}
                disabled={!hasFounder}
                className="sm:col-span-2 rounded-lg bg-violet-600 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
              >
                Open Raise Room (public ICO slots)
              </button>
              <p className="sm:col-span-2 text-xs text-zinc-500">
                Investors allocate paper dollars publicly. 1% burned on each commit — removed from circulation.
                Recharge paper dollars for $25 when balance drops below $1,000.
              </p>
            </div>
          )}
        </OsSection>
      )}

      {tab === 'agents' && session && (
        <AgentsWorkspacePanel accessToken={session.accessToken} founderActive={hasFounder} />
      )}

      {tab === 'agents' && !session && (
        <section className="rounded-2xl border border-dashed border-purple-500/40 bg-purple-950/10 p-8 text-center">
          <p className="text-sm text-zinc-400">
            <Link href="/login?callbackUrl=/founder-den?tab=agents" className="text-purple-300 underline">
              Sign in
            </Link>{' '}
            to create and run founder agents.
          </p>
        </section>
      )}

      {tab === 'build' && session && (
        <BuildRoom2
          accessToken={session.accessToken}
          founderCredits={dashboard?.founderCredits}
          communityRewardPool={dashboard?.communityRewardPool}
          projectId={room?.id}
          onRefresh={onRefresh}
          founderActive={hasFounder}
          onMessage={onWorkspaceMessage}
        />
      )}

      {tab === 'analytics' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <OsSection title="Founder profile" subtitle="Video · GitHub · Website · X" disabled={!hasFounder}>
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                value={appForm.videoUrl}
                onChange={(e) => setAppForm({ ...appForm, videoUrl: e.target.value })}
                placeholder="Intro video URL"
                disabled={hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={appForm.githubUrl}
                onChange={(e) => setAppForm({ ...appForm, githubUrl: e.target.value })}
                placeholder="GitHub"
                disabled={hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
            </div>
          </OsSection>

          <OsSection title="Project" subtitle="Activate to unlock workspace" disabled={false}>
            <input
              value={appForm.projectName}
              onChange={(e) => setAppForm({ ...appForm, projectName: e.target.value })}
              placeholder="Project name"
              disabled={hasFounder}
              className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
            />
            <textarea
              value={appForm.ideaDescription}
              onChange={(e) => setAppForm({ ...appForm, ideaDescription: e.target.value })}
              placeholder="What are you building?"
              rows={3}
              disabled={hasFounder}
              className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
            />
            <select
              value={appForm.lifecycleStage}
              onChange={(e) => setAppForm({ ...appForm, lifecycleStage: e.target.value })}
              disabled={hasFounder}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
            >
              {FOUNDER_STAGES.map((s) => (
                <option key={s} value={s}>
                  {LIFECYCLE_STAGES.find((x) => x.key === s)?.label ?? s}
                </option>
              ))}
            </select>
            {!hasFounder && session && (
              <button
                type="button"
                onClick={onSubmitApplication}
                className="mt-4 w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-semibold text-white"
              >
                Activate founder profile
              </button>
            )}
          </OsSection>

          {dashboard && (
            <OsSection title="Stats" subtitle="Progress · credits · readiness">
              <dl className="grid gap-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Followers</dt>
                  <dd className="text-white">{dashboard.followers ?? 0}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Founder credits</dt>
                  <dd className="text-emerald-300">{dashboard.founderCredits ?? 0}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Launch readiness</dt>
                  <dd className="text-white">
                    {room?.launchReadiness ?? dashboard.launchReadiness ?? 0}%
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Paper cash</dt>
                  <dd className="text-white">{formatUsd(dashboard.cashBalance, 0)}</dd>
                </div>
              </dl>
            </OsSection>
          )}
        </div>
      )}
        </>
      )}
    </div>
  );
}

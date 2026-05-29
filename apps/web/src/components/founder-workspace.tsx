'use client';

import Link from 'next/link';
import { formatUsd, LIFECYCLE_STAGES } from '@dcf/utils';
import { FounderJourneyProgress } from '@/components/founder-journey-progress';
import { FounderOsPanel } from '@/components/founder-os-panel';
import { AgentsWorkspacePanel } from '@/components/agents-workspace-panel';
import { DiscoverProjectCard } from '@/components/discover-project-card';
import {
  EcosystemPulse,
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
  { id: 'activity', label: 'Activity' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'community', label: 'Community' },
  { id: 'funding', label: 'Funding' },
  { id: 'agents', label: 'Agents' },
  { id: 'build', label: 'Build Room' },
  { id: 'analytics', label: 'Analytics' },
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
  pulse: EcosystemPulse | null;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  onRefresh: () => void;
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
    pulse,
    dashboard,
    room,
    onRefresh,
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-white">Founder Workspace</h2>
          <p className="text-sm text-zinc-500">Your daily command center — build, ship, fund, grow</p>
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

      <FounderJourneyProgress
        currentStage={currentStage}
        label={hasFounder ? 'Your journey' : 'The founder journey — start anywhere'}
      />

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
          <strong className="text-white">Activate founder profile</strong> in Analytics to unlock GitHub,
          Build Room, and integrations.
        </div>
      )}

      {tab === 'activity' && (
        <div className="space-y-6">
          {pulse && (
            <section className="grid gap-6 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
                  Recent founder activity
                </h3>
                <ul className="mt-3 space-y-3">
                  {pulse.recentActivity.slice(0, 8).map((post) => (
                    <li key={post.id} className="rounded-xl border border-zinc-800 bg-zinc-900/30 px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                        {post.dayNumber != null && (
                          <span className="font-semibold text-emerald-500">Day {post.dayNumber}</span>
                        )}
                        <Link href={`/founder/${post.founder.slug}`} className="hover:text-emerald-400">
                          {post.founder.name}
                        </Link>
                      </div>
                      <p className="mt-1 font-medium text-white">{post.headline}</p>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">
                  Ecosystem pulse
                </h3>
                <div className="mt-3 space-y-2 rounded-xl border border-zinc-800 bg-zinc-900/30 p-4 text-sm">
                  <p className="flex justify-between">
                    <span className="text-zinc-500">Live tokens</span>
                    <span className="font-semibold text-purple-300">{pulse.liveTokenCount}</span>
                  </p>
                  <p className="flex justify-between">
                    <span className="text-zinc-500">Building</span>
                    <span className="font-semibold text-emerald-300">{pulse.buildingCount}</span>
                  </p>
                  <p className="flex justify-between">
                    <span className="text-zinc-500">Idea stage</span>
                    <span className="font-semibold text-blue-300">{pulse.ideaCount}</span>
                  </p>
                </div>
              </div>
            </section>
          )}
          {pulse && pulse.trendingProjects.length > 0 && (
            <section>
              <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Trending</h3>
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {pulse.trendingProjects.slice(0, 3).map((p) => (
                  <DiscoverProjectCard key={p.slug} project={p} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {tab === 'tasks' && session && (
        <OsSection title="Open tasks" subtitle="GitHub suggestions · bounties · publish queue" disabled={!hasFounder}>
          <p className="text-sm text-zinc-400">
            Sync GitHub in Build Room to generate suggested updates. Create bounties to delegate work to
            the community.
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
          <Link href="/build-feed" className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
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
        <OsSection title="Funding & demand" subtitle="Simulated raise · launch readiness" disabled={!hasFounder}>
          {room?.activeRaise ? (
            <div className="text-sm text-zinc-300">
              <p>Goal {formatUsd(room.activeRaise.goalUsd, 0)}</p>
              <p className="mt-1">
                Demand {formatUsd(room.activeRaise.totalAllocated, 0)} ·{' '}
                {room.activeRaise.allocatorCount} backers
              </p>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                value={raiseForm.goalUsd}
                onChange={(e) => setRaiseForm({ ...raiseForm, goalUsd: e.target.value })}
                placeholder="Funding goal"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <input
                value={raiseForm.durationDays}
                onChange={(e) => setRaiseForm({ ...raiseForm, durationDays: e.target.value })}
                placeholder="Days"
                disabled={!hasFounder}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50"
              />
              <button
                type="button"
                onClick={onLaunchRaise}
                disabled={!hasFounder}
                className="sm:col-span-2 rounded-lg border border-emerald-500/40 py-2 text-sm text-emerald-200 disabled:opacity-40"
              >
                Launch simulated raise
              </button>
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
        <FounderOsPanel
          accessToken={session.accessToken}
          founderCredits={dashboard?.founderCredits}
          communityRewardPool={dashboard?.communityRewardPool}
          projectId={room?.id}
          onRefresh={onRefresh}
          founderActive={hasFounder}
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
    </div>
  );
}

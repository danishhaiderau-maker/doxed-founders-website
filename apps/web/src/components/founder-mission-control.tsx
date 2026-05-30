'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  formatUsd,
  formatRelativeTime,
  getStageColorLabel,
  getStageColorTheme,
  LIFECYCLE_STAGES,
  STAGE_COLOR_CLASSES,
} from '@dcf/utils';
import { FounderCopilotBriefing } from '@/components/founder-copilot-briefing';
import { FounderInboxPanel } from '@/components/founder-inbox-panel';
import {
  BuildRoomData,
  fetchBuildRoom,
  FounderDashboard,
  ProjectRoom,
  updateBuildQueueItem,
} from '@/lib/api';
import type { WorkspaceTab } from '@/components/founder-workspace';

export type FounderMissionControlProps = {
  session: { accessToken: string } | null;
  hasFounder: boolean;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  onTabChange: (tab: WorkspaceTab) => void;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
};

function stageLabel(key: string) {
  return LIFECYCLE_STAGES.find((s) => s.key === key)?.label ?? key.replace(/_/g, ' ');
}

export function FounderMissionControl({
  session,
  hasFounder,
  dashboard,
  room,
  onTabChange,
  onRefresh,
  onMessage,
}: FounderMissionControlProps) {
  const [buildRoom, setBuildRoom] = useState<BuildRoomData | null>(null);
  const [taskBusy, setTaskBusy] = useState<string | null>(null);

  const loadBuildRoom = useCallback(async () => {
    if (!session?.accessToken || !hasFounder) {
      setBuildRoom(null);
      return;
    }
    try {
      setBuildRoom(await fetchBuildRoom(session.accessToken));
    } catch {
      setBuildRoom(null);
    }
  }, [session?.accessToken, hasFounder]);

  useEffect(() => {
    loadBuildRoom();
  }, [loadBuildRoom]);

  async function markTaskDone(id: string) {
    if (!session?.accessToken) return;
    setTaskBusy(id);
    try {
      await updateBuildQueueItem(id, { status: 'DONE' }, session.accessToken);
      onMessage?.('Task marked done');
      loadBuildRoom();
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not update task');
    } finally {
      setTaskBusy(null);
    }
  }

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

  const stage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';
  const theme = getStageColorTheme(stage, room?.isLiveToken);
  const colors = STAGE_COLOR_CLASSES[theme];
  const progress = room?.launchReadiness ?? dashboard?.launchReadiness ?? 0;
  const demand = room?.activeRaise
    ? room.activeRaise.totalAllocated
    : dashboard?.simulatedDemand ?? 0;
  const followers = room?.followerCount ?? dashboard?.followers ?? 0;
  const lastCommit = buildRoom?.commits[0];
  const openTasks = buildRoom?.grouped.tasks.filter((t) => t.status !== 'DONE') ?? [];
  const recentPosts = room?.buildPosts?.slice(0, 5) ?? [];

  return (
    <div className="space-y-6">
      {room && (
        <section className={`rounded-2xl border ${colors.badge} p-5 sm:p-6`}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
                Current project
              </p>
              <Link
                href={`/project/${room.slug}`}
                className="mt-1 text-2xl font-bold text-white hover:text-blue-300"
              >
                {room.name}
              </Link>
              <p className="mt-1 text-sm text-zinc-500">${room.ticker}</p>
            </div>
            <span
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${colors.badge} ${colors.text}`}
            >
              {getStageColorLabel(theme)} · {stageLabel(stage)}
            </span>
          </div>

          <div className="mt-5">
            <div className="flex items-center justify-between text-xs text-zinc-500">
              <span>Progress</span>
              <span className={`font-bold ${colors.text}`}>{progress}%</span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-800">
              <div
                className={`h-full rounded-full ${theme === 'live' ? 'bg-purple-500' : theme === 'validation' ? 'bg-amber-500' : theme === 'launch_ready' ? 'bg-emerald-500' : 'bg-blue-500'}`}
                style={{ width: `${Math.min(100, progress)}%` }}
              />
            </div>
          </div>

          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCell
              label="Last commit"
              value={lastCommit ? lastCommit.message.split('\n')[0] : 'Connect GitHub'}
              sub={lastCommit ? formatRelativeTime(lastCommit.date) : undefined}
            />
            <StatCell
              label="Deployment"
              value={
                buildRoom?.githubConnected
                  ? buildRoom.deployments.length > 0
                    ? `${buildRoom.deployments.length} draft${buildRoom.deployments.length === 1 ? '' : 's'} ready`
                    : 'Stack connected'
                  : 'Not connected'
              }
              sub={buildRoom?.repoFullName ?? undefined}
            />
            <StatCell label="Demand" value={formatUsd(demand, 0)} sub="Raise Room · Ddollar" />
            <StatCell label="Followers" value={String(followers)} />
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              href={`/project/${room.slug}`}
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:text-white"
            >
              Project room →
            </Link>
            <Link
              href="/settings/builder"
              className="rounded-lg border border-violet-500/30 px-3 py-1.5 text-xs text-violet-200"
            >
              Connect stack →
            </Link>
          </div>
        </section>
      )}

      <FounderCopilotBriefing
        accessToken={session.accessToken}
        onMessage={onMessage}
        onRefresh={() => {
          loadBuildRoom();
          onRefresh();
        }}
      />

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-white">Continue building</h3>
            <p className="mt-1 text-xs text-zinc-500">Suggested next tasks from your build queue</p>
          </div>
          <button
            type="button"
            onClick={() => onTabChange('build')}
            className="text-xs font-medium text-blue-400 hover:underline"
          >
            Open Founder Copilot →
          </button>
        </div>

        {openTasks.length === 0 ? (
          <p className="mt-4 text-sm text-zinc-500">
            No open tasks yet. Use Quick Build or Founder Copilot to capture what&apos;s next.
          </p>
        ) : (
          <ul className="mt-4 space-y-2">
            {openTasks.slice(0, 6).map((task) => (
              <li
                key={task.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-black/20 px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-white">{task.title}</p>
                  <p className="text-[10px] uppercase text-zinc-600">{task.status.replace('_', ' ')}</p>
                </div>
                <button
                  type="button"
                  disabled={taskBusy === task.id}
                  onClick={() => markTaskDone(task.id)}
                  className="shrink-0 rounded border border-emerald-500/40 px-2 py-1 text-[10px] text-emerald-300 disabled:opacity-50"
                >
                  {taskBusy === task.id ? '…' : 'Done'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-semibold text-white">Founder feed</h3>
            <Link href="/feed" className="text-xs text-blue-400 hover:underline">
              Feed →
            </Link>
          </div>
          <p className="mt-1 text-xs text-zinc-500">Your published updates</p>
          {recentPosts.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">
              No posts yet.{' '}
              <button
                type="button"
                onClick={() => onTabChange('community')}
                className="text-blue-400 hover:underline"
              >
                Publish an update
              </button>
            </p>
          ) : (
            <ul className="mt-4 space-y-3">
              {recentPosts.map((post) => (
                <li key={post.id} className="rounded-lg border border-zinc-800 px-3 py-2.5">
                  <p className="text-sm font-medium text-white">{post.headline}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-zinc-500">{post.body}</p>
                  <p className="mt-1 text-[10px] text-zinc-600">
                    {formatRelativeTime(post.publishedAt)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-2xl border border-violet-500/25 bg-violet-950/10 p-5">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-semibold text-white">Raise Room</h3>
            <button
              type="button"
              onClick={() => onTabChange('funding')}
              className="text-xs text-violet-300 hover:underline"
            >
              Manage →
            </button>
          </div>
          {room?.activeRaise ? (
            <div className="mt-4">
              <p className="text-2xl font-bold text-violet-200">
                {formatUsd(room.activeRaise.totalAllocated, 0)}
              </p>
              <p className="text-xs text-zinc-500">
                of {formatUsd(room.activeRaise.goalUsd, 0)} goal ·{' '}
                {room.activeRaise.allocatorCount} allocators
              </p>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-violet-500"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round((room.activeRaise.totalAllocated / room.activeRaise.goalUsd) * 100),
                    )}%`,
                  }}
                />
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm text-zinc-400">
              No active raise. Open a simulated raise to validate demand before launch.
            </p>
          )}
        </section>
      </div>

      <section className="rounded-2xl border border-purple-500/25 bg-purple-950/10 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-white">Agents</h3>
            <p className="mt-1 text-xs text-zinc-500">Workforce agents for specs, content, and ops</p>
          </div>
          <button
            type="button"
            onClick={() => onTabChange('agents')}
            className="rounded-lg border border-purple-500/40 px-3 py-1.5 text-xs text-purple-200 hover:bg-purple-950/40"
          >
            Open agent workspace
          </button>
        </div>
        <Link href="/agents" className="mt-3 inline-block text-xs text-purple-300 hover:underline">
          Browse agent marketplace →
        </Link>
      </section>

      <FounderInboxPanel accessToken={session.accessToken} />
    </div>
  );
}

function StatCell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-black/25 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-wider text-zinc-600">{label}</p>
      <p className="mt-0.5 truncate text-sm font-medium text-zinc-200">{value}</p>
      {sub && <p className="truncate text-[10px] text-zinc-600">{sub}</p>}
    </div>
  );
}

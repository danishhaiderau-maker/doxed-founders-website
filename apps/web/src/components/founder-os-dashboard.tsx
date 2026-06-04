'use client';

import Link from 'next/link';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import {
  formatRelativeTime,
  getStageColorLabel,
  getStageColorTheme,
  LIFECYCLE_STAGES,
  resolveAiStackHealth,
  STAGE_COLOR_CLASSES,
} from '@dcf/utils';
import { AutopilotPromoToast } from '@/components/autopilot-promo-toast';
import { FounderCopilotChat } from '@/components/founder-copilot-chat';
import { FounderCommandCenterPanels } from '@/components/founder-command-center-panels';
import { MissionStatePanel } from '@/components/mission-state-panel';
import { FounderOsReadinessPanel } from '@/components/founder-os-readiness-panel';
import { MissionControlStatusStrip } from '@/components/mission-control-status-strip';
import { MissionControlTrustStrip } from '@/components/mission-control-trust-strip';
import type { WorkspaceTab } from '@/components/founder-workspace';
import {
  BuildRoomData,
  copilotMissionBuild,
  copilotResume,
  fetchAccountOverview,
  fetchBuildRoom,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  fetchMissionIntelligence,
  fetchFounderQueue,
  fetchAttentionCenter,
  executeFounderQueueAction,
  fetchPlatformSyncStatus,
  type MissionIntelligence,
  type FounderQueueItem,
  type AttentionItem,
  updateBuilderSettings,
  FounderDashboard,
  ProjectMemory,
  ProjectRoom,
} from '@/lib/api';
import { pollMissionBuildUntilDone } from '@/lib/mission-build-runner';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

type NavItem = { id: WorkspaceTab; label: string; icon: string };

const SIDEBAR_NAV: NavItem[] = [
  { id: 'activity', label: 'Mission Control', icon: '◆' },
  { id: 'social', label: 'Social Hub', icon: '📡' },
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'analytics', label: 'Settings', icon: '⚙' },
];

const COPILOT_CHIPS = [
  'What am I working on?',
  'What should I ship today?',
  'What broke yesterday?',
  'Continue last task',
  'Run platform autopilot sync',
];

export type FounderOsDashboardProps = {
  accessToken: string;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
  tabContent?: ReactNode;
  initialCopilotPrompt?: string | null;
  onInitialCopilotPromptConsumed?: () => void;
  activeAgentTemplate?: string | null;
};

function stageLabel(key: string) {
  return LIFECYCLE_STAGES.find((s) => s.key === key)?.label ?? key.replace(/_/g, ' ');
}

export function FounderOsDashboardLayout({
  accessToken,
  dashboard,
  room,
  activeTab,
  onTabChange,
  onRefresh,
  onMessage,
  tabContent,
  initialCopilotPrompt,
  onInitialCopilotPromptConsumed,
  activeAgentTemplate: _activeAgentTemplate,
}: FounderOsDashboardProps) {
  const [buildRoom, setBuildRoom] = useState<BuildRoomData | null>(null);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [workerStatus, setWorkerStatus] = useState<Awaited<
    ReturnType<typeof fetchBuilderWorkerStatus>
  > | null>(null);
  const [quickPrompt, setQuickPrompt] = useState<string | null>(initialCopilotPrompt ?? null);
  const [chatKey, setChatKey] = useState(0);
  const [username, setUsername] = useState<string>('@founder');
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const [roleLabel, setRoleLabel] = useState<string>('Founder');
  const [resumeBusy, setResumeBusy] = useState(false);
  const [missionBuildBusy, setMissionBuildBusy] = useState(false);
  const [syncStatus, setSyncStatus] = useState<Awaited<
    ReturnType<typeof fetchPlatformSyncStatus>
  > | null>(null);
  const [missionIntel, setMissionIntel] = useState<MissionIntelligence | null>(null);
  const [founderQueue, setFounderQueue] = useState<FounderQueueItem[]>([]);
  const [attentionItems, setAttentionItems] = useState<AttentionItem[]>([]);
  const [attentionUrgent, setAttentionUrgent] = useState(0);
  const [commandCenterLoading, setCommandCenterLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [br, mem, worker, account, platform, intel, queueRes, attentionRes] =
        await Promise.all([
        fetchBuildRoom(accessToken),
        fetchCopilotMemory(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
        fetchAccountOverview(accessToken).catch(() => null),
        fetchPlatformSyncStatus(accessToken).catch(() => null),
        fetchMissionIntelligence(accessToken).catch(() => null),
        fetchFounderQueue(accessToken).catch(() => null),
        fetchAttentionCenter(accessToken).catch(() => null),
      ]);
      setBuildRoom(br);
      setMemory(mem);
      setWorkerStatus(worker);
      setSyncStatus(platform);
      setMissionIntel(intel);
      setFounderQueue(queueRes?.items ?? []);
      setAttentionItems(attentionRes?.items ?? []);
      setAttentionUrgent(attentionRes?.urgentCount ?? 0);
      setCommandCenterLoading(false);
      if (account) {
        setUsername(account.username.startsWith('@') ? account.username : `@${account.username}`);
        setAvatarUrl(account.avatarUrl);
        setRoleLabel(account.gamifiedRole?.label ?? 'Founder');
      }
    } catch {
      setBuildRoom(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!initialCopilotPrompt?.trim()) return;
    setQuickPrompt(initialCopilotPrompt);
  }, [initialCopilotPrompt]);

  const stage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';
  const theme = getStageColorTheme(stage, room?.isLiveToken);
  const colors = STAGE_COLOR_CLASSES[theme];
  const readiness = memory?.launchReadiness ?? room?.launchReadiness ?? dashboard?.launchReadiness ?? 0;
  const openTasks = buildRoom?.grouped.tasks.filter((t) => t.status !== 'DONE').length ?? 0;
  const currentGoal = memory?.currentGoal ?? 'Set your current goal in Settings';
  const openBuilderTask = buildRoom?.grouped.tasks.find((t) => t.status !== 'DONE');

  const aiStackHealth = useMemo(
    () =>
      resolveAiStackHealth({
        llmConnected: workerStatus?.llmConnected ?? false,
        buildWorker: (workerStatus?.buildWorker as 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE') ?? 'NONE',
        githubConnected: workerStatus?.githubConnected ?? buildRoom?.githubConnected ?? false,
      }),
    [workerStatus, buildRoom],
  );

  const showMissionControl = activeTab === 'activity' && !tabContent;

  async function handleResumeWork() {
    setResumeBusy(true);
    try {
      const result = await copilotResume(accessToken);
      setQuickPrompt(result.message);
      setChatKey((k) => k + 1);
      onMessage?.(result.dispatchHint ?? result.message);

      const mb = result.missionBuild;
      if (mb?.status === 'dispatched' && mb.agentId && mb.runId) {
        await pollMissionBuildUntilDone(
          accessToken,
          {
            graph: result.memory.memoryGraph!,
            taskLabel: mb.taskLabel,
            status: mb.status,
            worker: 'CURSOR',
            message: result.message,
            agentId: mb.agentId,
            runId: mb.runId,
          },
          (line) => onMessage?.(line),
        );
      } else if (mb?.status === 'dispatched' && mb.conversationId) {
        await pollMissionBuildUntilDone(
          accessToken,
          {
            graph: result.memory.memoryGraph!,
            taskLabel: mb.taskLabel,
            status: mb.status,
            worker: 'OPENHANDS',
            message: result.message,
            conversationId: mb.conversationId,
          },
          (line) => onMessage?.(line),
        );
      }

      void load();
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Resume failed');
    } finally {
      setResumeBusy(false);
    }
  }

  async function handleSidebarMissionBuild() {
    setMissionBuildBusy(true);
    try {
      const worker =
        workerStatus?.buildWorker === 'CURSOR' || workerStatus?.buildWorker === 'OPENHANDS'
          ? workerStatus.buildWorker
          : undefined;
      const result = await copilotMissionBuild(accessToken, worker ? { worker } : undefined);
      onMessage?.(result.message);
      if (result.status === 'dispatched') {
        await pollMissionBuildUntilDone(accessToken, result, (line) => onMessage?.(line));
      }
      void load();
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Build failed');
    } finally {
      setMissionBuildBusy(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#07070a]">
      <div className="flex flex-1 flex-col lg:flex-row">
        <aside className="flex w-full shrink-0 flex-col border-b border-zinc-800/80 bg-[#0a0a0e] lg:w-56 lg:border-b-0 lg:border-r xl:w-60">
          <div className="border-b border-zinc-800/60 px-4 py-4">
            <Link href="/" className="text-sm font-bold tracking-tight text-white hover:text-violet-300">
              DOXXED
            </Link>
            <p className="mt-0.5 text-[10px] text-zinc-600">Founder OS</p>
          </div>

          <nav className="flex-1 space-y-0.5 p-2">
            {SIDEBAR_NAV.map((item) => {
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onTabChange(item.id)}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition ${
                    active
                      ? 'bg-violet-600/20 font-medium text-violet-200'
                      : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
                  }`}
                >
                  <span className="text-base opacity-80">{item.icon}</span>
                  {item.label}
                </button>
              );
            })}
          </nav>

          {showMissionControl && room && (
            <div className="border-t border-zinc-800/60 p-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">
                Project status
              </p>
              <p className="mt-1 truncate text-sm font-semibold text-white">{room.name}</p>
              <div className="mt-3">
                <div className="flex items-center justify-between text-[10px] text-zinc-500">
                  <span>Launch readiness</span>
                  <span className="font-semibold text-emerald-400">{readiness}%</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className="h-full rounded-full bg-violet-500"
                    style={{ width: `${Math.min(100, readiness)}%` }}
                  />
                </div>
              </div>
              <p className="mt-3 text-[10px] text-zinc-600">Current goal</p>
              <p className="mt-0.5 text-xs leading-snug text-zinc-300 line-clamp-3">{currentGoal}</p>
            </div>
          )}

          <div className="mt-auto border-t border-zinc-800/60 p-3">
            <div className="flex items-center gap-2.5 rounded-lg px-2 py-2">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-violet-600/30 text-sm font-semibold text-violet-200">
                {avatarUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={avatarUrl} alt="" className="h-full w-full object-cover" />
                ) : (
                  username.slice(1, 2).toUpperCase()
                )}
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-white">{username}</p>
                <p className="text-[10px] text-zinc-600">{roleLabel}</p>
              </div>
            </div>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {showMissionControl ? (
            <div className="mx-auto flex max-w-[90rem] flex-col gap-6">
              <header className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
                    Founder Command Center
                  </p>
                  <h1 className="mt-1 text-xl font-bold text-white sm:text-2xl">
                    Think · plan · build · ship — in one tab
                  </h1>
                  <p className="mt-1 text-xs text-zinc-500">
                    Founder Brain routes research, build, and strategy — you stay in chat.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={resumeBusy || missionBuildBusy}
                    onClick={() => void handleResumeWork()}
                    className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
                  >
                    {resumeBusy ? 'Resuming…' : '▶ Resume Work'}
                  </button>
                  <button
                    type="button"
                    disabled={missionBuildBusy || resumeBusy}
                    onClick={() => void handleSidebarMissionBuild()}
                    className="rounded-xl border border-violet-500/40 px-4 py-2.5 text-sm font-medium text-violet-200 hover:bg-violet-950/40 disabled:opacity-50"
                  >
                    {missionBuildBusy ? 'Building…' : 'Run build'}
                  </button>
                </div>
              </header>

              <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(280px,32%)]">
                <div className="flex min-w-0 flex-col gap-4">
                  <FounderCopilotChat
                    key={chatKey}
                    accessToken={accessToken}
                    variant="hero"
                    memory={memory}
                    initialPrompt={quickPrompt}
                    agentTemplate={null}
                    onInitialPromptConsumed={() => {
                      setQuickPrompt(null);
                      onInitialCopilotPromptConsumed?.();
                    }}
                    onResult={(a) => {
                      onMessage?.(a);
                      void load();
                      onRefresh();
                    }}
                  />
                  <div className="flex flex-wrap gap-2">
                    {COPILOT_CHIPS.map((chip) => (
                      <button
                        key={chip}
                        type="button"
                        onClick={() => setQuickPrompt(chip)}
                        className="rounded-full border border-zinc-700/80 bg-zinc-900/50 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-violet-500/50 hover:text-white"
                      >
                        {chip}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex flex-col gap-4">
                  <FounderCommandCenterPanels
                    queue={founderQueue}
                    attention={attentionItems}
                    urgentCount={attentionUrgent}
                    loading={commandCenterLoading}
                    onPrompt={(prompt) => {
                      setQuickPrompt(prompt);
                      setChatKey((k) => k + 1);
                    }}
                    onQueueAction={async (itemId) => {
                      const result = await executeFounderQueueAction(itemId, accessToken);
                      onMessage?.(result.message);
                      void load();
                      onRefresh();
                      return { message: result.message };
                    }}
                  />

                  {missionIntel && (
                    <div className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-4 text-sm text-zinc-200">
                      <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300">
                        Mission intelligence
                      </p>
                      <p className="mt-2 font-medium text-white">{missionIntel.currentInitiative}</p>
                      <p className="mt-2 text-xs text-zinc-400">{missionIntel.impact}</p>
                      {missionIntel.blocker && (
                        <p className="mt-2 text-xs text-amber-200/90">Blocker: {missionIntel.blocker}</p>
                      )}
                      <p className="mt-2 text-xs text-emerald-200/90">
                        Next: {missionIntel.recommendedNextStep}
                      </p>
                      <p className="mt-2 text-[10px] text-zinc-600">
                        Confidence: {missionIntel.confidence} · {missionIntel.progressPercent}% progress
                      </p>
                    </div>
                  )}

                  <MissionStatePanel
                    accessToken={accessToken}
                    initial={memory?.memoryGraph ?? null}
                    lastCommit={memory?.lastCommit}
                    openTaskCount={memory?.openTasks?.length ?? 0}
                    buildWorker={workerStatus?.buildWorker}
                    workerReady={workerStatus?.buildWorker !== 'NONE'}
                    repoFullName={memory?.repoFullName}
                    onSaved={() => void load()}
                    onBuildComplete={(msg) => {
                      onMessage?.(msg);
                      void load();
                      onRefresh();
                    }}
                  />

                  <MissionControlStatusStrip
                    accessToken={accessToken}
                    buildWorker={workerStatus?.buildWorker}
                    onRefresh={() => {
                      void load();
                      onRefresh();
                    }}
                  />

                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { label: 'Open tasks', value: String(openTasks) },
                      { label: 'Agents', value: workerStatus?.buildWorker !== 'NONE' ? '1' : '0' },
                      { label: 'Commits (7d)', value: String(buildRoom?.stats.commits ?? 0) },
                      { label: 'Readiness', value: `${readiness}%` },
                    ].map((stat) => (
                      <div
                        key={stat.label}
                        className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-3 py-2.5"
                      >
                        <p className="text-[10px] uppercase tracking-wider text-zinc-600">{stat.label}</p>
                        <p className="mt-0.5 truncate text-sm font-semibold text-white">{stat.value}</p>
                      </div>
                    ))}
                  </div>

                  <FounderOsReadinessPanel
                    accessToken={accessToken}
                    onRefresh={() => {
                      void load();
                      onRefresh();
                    }}
                  />

                  <MissionControlTrustStrip
                    accessToken={accessToken}
                    onRefresh={() => {
                      void load();
                      onRefresh();
                    }}
                  />
                </div>
              </div>
            </div>
          ) : (
            tabContent ?? (
              <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-6 text-center text-sm text-zinc-500">
                Select a section from the sidebar.
              </div>
            )
          )}
        </main>

        {showMissionControl && (
          <aside className="hidden w-72 shrink-0 border-l border-zinc-800/80 bg-[#0a0a0e] p-4 xl:block">
            <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Active agents
              </h3>
              <div className="mt-3 space-y-3">
                <div className="rounded-lg border border-violet-500/25 bg-violet-950/20 p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-white">Builder Agent</p>
                    <span
                      className={`text-[10px] font-semibold ${
                        workerStatus?.buildWorker !== 'NONE' ? 'text-emerald-400' : 'text-zinc-600'
                      }`}
                    >
                      {openBuilderTask && workerStatus?.buildWorker !== 'NONE'
                        ? 'Working'
                        : workerStatus?.buildWorker !== 'NONE'
                          ? 'Ready'
                          : 'Offline'}
                    </span>
                  </div>
                  {openBuilderTask && (
                    <p className="mt-2 text-xs text-zinc-400">
                      {openBuilderTask.title.slice(0, 56)}
                    </p>
                  )}
                  {workerStatus?.cursorAgentUrl && (
                    <a
                      href={workerStatus.cursorAgentUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-block text-[10px] text-violet-400 hover:underline"
                    >
                      View run →
                    </a>
                  )}
                  <button
                    type="button"
                    disabled={missionBuildBusy || workerStatus?.buildWorker === 'NONE'}
                    onClick={() => void handleSidebarMissionBuild()}
                    className="mt-3 w-full rounded-lg bg-violet-600/80 py-2 text-[11px] font-semibold text-white hover:bg-violet-600 disabled:opacity-40"
                  >
                    {missionBuildBusy ? 'Running…' : 'Run current task'}
                  </button>
                  {workerStatus?.buildWorker === 'NONE' && (
                    <Link
                      href={AI_STACK_HREF}
                      className="mt-2 block text-center text-[10px] text-zinc-500 hover:text-violet-300"
                    >
                      Connect builder →
                    </Link>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => onTabChange('agents')}
                  className="w-full rounded-lg border border-zinc-800 py-2 text-[11px] text-zinc-400 hover:text-white"
                >
                  Open Agent Workforce
                </button>
              </div>
            </section>

            <section className="mt-4 space-y-2">
              {[
                {
                  label: 'GitHub sync',
                  ok: buildRoom?.githubConnected ?? false,
                  detail: buildRoom?.repoFullName ?? 'Not linked',
                },
                {
                  label: 'AI stack',
                  ok: aiStackHealth === 'healthy',
                  detail:
                    aiStackHealth === 'healthy'
                      ? 'Healthy'
                      : aiStackHealth === 'needs_attention'
                        ? 'Needs attention'
                        : 'Connect in Settings',
                },
                {
                  label: 'Founder Node',
                  ok: workerStatus?.connections.founderNode ?? false,
                  detail: workerStatus?.connections.founderNode ? 'Connected' : 'Optional',
                },
              ].map((card) => (
                <div
                  key={card.label}
                  className="flex items-center justify-between rounded-lg border border-zinc-800/80 bg-zinc-900/30 px-3 py-2.5"
                >
                  <div>
                    <p className="text-xs text-zinc-300">{card.label}</p>
                    <p className="text-[10px] text-zinc-600 truncate max-w-[140px]">{card.detail}</p>
                  </div>
                  <span className={card.ok ? 'text-emerald-400' : 'text-zinc-600'}>
                    {card.ok ? '●' : '○'}
                  </span>
                </div>
              ))}
            </section>

            {room && (
              <section className={`mt-4 rounded-xl border p-3 ${colors.badge}`}>
                <p className="text-[10px] text-zinc-600">Stage</p>
                <span className={`text-xs font-semibold ${colors.text}`}>
                  {getStageColorLabel(theme)} · {stageLabel(stage)}
                </span>
                {memory?.lastCommit && (
                  <p className="mt-2 text-[10px] text-zinc-500">
                    Last commit · {memory.lastCommit.split('\n')[0].slice(0, 48)}
                  </p>
                )}
                {memory?.lastActivityLabel && (
                  <p className="mt-1 text-[10px] text-zinc-600">
                    Synced {formatRelativeTime(memory.lastActivityAt ?? undefined)}
                  </p>
                )}
              </section>
            )}

            <Link
              href="/founder-den?tab=analytics"
              className="mt-4 block text-center text-[10px] text-zinc-600 hover:text-violet-400"
            >
              Technical settings →
            </Link>
          </aside>
        )}
      </div>

      <AutopilotPromoToast
        show={showMissionControl}
        autopilotEnabled={syncStatus?.autopilotEnabled ?? false}
        pendingPublishCount={syncStatus?.pendingPublishCount}
        onEnable={async () => {
          try {
            await updateBuilderSettings(
              {
                autopilotEnabled: true,
                autopilotRedeployHosts: true,
                autoPublishOnEvent: true,
              },
              accessToken,
            );
            onMessage?.('Autopilot enabled');
            void load();
          } catch (err) {
            onMessage?.(err instanceof Error ? err.message : 'Failed to enable autopilot');
          }
        }}
      />

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/80 bg-[#0a0a0e] px-4 py-2 text-[10px] text-zinc-600">
        <span className="flex items-center gap-1.5">
          <span className="text-emerald-500">●</span> All systems operational
        </span>
        <span>
          AI stack ·{' '}
          {aiStackHealth === 'healthy'
            ? 'Healthy'
            : aiStackHealth === 'needs_attention'
              ? 'Needs attention'
              : 'Offline'}
        </span>
      </footer>
    </div>
  );
}

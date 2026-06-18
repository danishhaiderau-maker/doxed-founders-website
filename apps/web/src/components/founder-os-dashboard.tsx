'use client';

import Link from 'next/link';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import {
  resolveAiStackHealth,
  isAgentRunActive,
  isStaleBoilerplateMissionTask,
  type ChiefOfStaffNudge,
} from '@dcf/utils';
import { AutopilotPromoToast } from '@/components/autopilot-promo-toast';
import { FounderCopilotChat } from '@/components/founder-copilot-chat';
import { FounderCommandCenterPanels } from '@/components/founder-command-center-panels';
import { FounderPersonalGuide } from '@/components/founder-personal-guide';
import { AgentRunStepsPanel } from '@/components/agent-run-steps-panel';
import { FounderGraphMiniPanel } from '@/components/founder-graph-mini-panel';
import { MissionControlConnectionHub } from '@/components/mission-control-connection-hub';
import type { WorkspaceTab } from '@/components/founder-workspace';
import {
  BuildRoomData,
  fetchAccountOverview,
  fetchBuildRoom,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  fetchCopilotStandup,
  fetchChiefOfStaffNudges,
  fetchMissionIntelligence,
  fetchFounderQueue,
  executeFounderQueueAction,
  fetchActiveAgentRun,
  fetchFounderGraph,
  fetchPlatformSyncStatus,
  fetchFounderPromoStatus,
  type FounderPromoUserStatus,
  type FounderGraphResponse,
  type MissionIntelligence,
  type FounderQueueItem,
  type FounderAgentRunRecord,
  updateBuilderSettings,
  FounderDashboard,
  ProjectMemory,
  ProjectRoom,
} from '@/lib/api';
import {
  connectionSnapshotFromSync,
  resolveSmartQuickPrompts,
  type ProviderRow,
} from '@/lib/copilot-ai-stack';

type NavItem = { id: WorkspaceTab; label: string; icon: string };

const SIDEBAR_NAV: NavItem[] = [
  { id: 'activity', label: 'Mission Control', icon: '◆' },
  { id: 'social', label: 'Social Hub', icon: '📡' },
  { id: 'analytics', label: 'Settings', icon: '⚙' },
];

const EXECUTIVE_BRIEF_DATE_KEY = 'dcf-executive-brief-date-v1';

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
  const [resumeBriefing, setResumeBriefing] = useState<string | null>(null);
  const [executiveBrief, setExecutiveBrief] = useState<string | null>(null);
  const [chatKey, setChatKey] = useState(0);
  const [username, setUsername] = useState<string>('@founder');
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const [roleLabel, setRoleLabel] = useState<string>('Founder');
  const [aiProviders, setAiProviders] = useState<ProviderRow[]>([]);
  const [defaultAiProvider, setDefaultAiProvider] = useState('RULE_BASED');
  const [syncStatus, setSyncStatus] = useState<Awaited<
    ReturnType<typeof fetchPlatformSyncStatus>
  > | null>(null);
  const [missionIntel, setMissionIntel] = useState<MissionIntelligence | null>(null);
  const [founderQueue, setFounderQueue] = useState<FounderQueueItem[]>([]);
  const [commandCenterLoading, setCommandCenterLoading] = useState(true);
  const [activeAgentRun, setActiveAgentRun] = useState<FounderAgentRunRecord | null>(null);
  const [founderGraph, setFounderGraph] = useState<FounderGraphResponse | null>(null);
  const [liveNudges, setLiveNudges] = useState<ChiefOfStaffNudge[]>([]);
  const [promoStatus, setPromoStatus] = useState<FounderPromoUserStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const [br, mem, worker, builder, account, platform, intel, queueRes, agentRunRes, graphRes, promo] =
        await Promise.all([
        fetchBuildRoom(accessToken),
        fetchCopilotMemory(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
        fetchBuilderSettings(accessToken).catch(() => null),
        fetchAccountOverview(accessToken).catch(() => null),
        fetchPlatformSyncStatus(accessToken).catch(() => null),
        fetchMissionIntelligence(accessToken).catch(() => null),
        fetchFounderQueue(accessToken).catch(() => null),
        fetchActiveAgentRun(accessToken).catch(() => null),
        fetchFounderGraph(accessToken).catch(() => null),
        fetchFounderPromoStatus(accessToken).catch(() => null),
      ]);
      setBuildRoom(br);
      setMemory(mem);
      setWorkerStatus(worker);
      if (builder) {
        setAiProviders(builder.providers);
        setDefaultAiProvider(builder.defaultProvider);
      }
      setSyncStatus(platform);
      setMissionIntel(intel);
      setFounderQueue(queueRes?.items ?? []);
      setActiveAgentRun(agentRunRes?.active ? agentRunRes.run : null);
      setFounderGraph(graphRes);
      setPromoStatus(promo);
      setCommandCenterLoading(false);
      if (account) {
        setUsername(account.username);
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
    if (!isAgentRunActive(activeAgentRun)) return;
    const id = window.setInterval(() => void load(), 8000);
    return () => window.clearInterval(id);
  }, [activeAgentRun, load]);

  useEffect(() => {
    if (!initialCopilotPrompt?.trim()) return;
    setQuickPrompt(initialCopilotPrompt);
  }, [initialCopilotPrompt]);

  const readiness = memory?.launchReadiness ?? room?.launchReadiness ?? dashboard?.launchReadiness ?? 0;
  const openTasks = buildRoom?.grouped.tasks.filter((t) => t.status !== 'DONE').length ?? 0;
  const currentGoal = memory?.currentGoal ?? 'Set your current goal in Settings';
  const displayInitiative =
    missionIntel?.currentInitiative?.trim() || currentGoal;
  const rawNextStep =
    missionIntel?.recommendedNextStep?.trim() || memory?.suggestedNextStep || null;
  const displayNextStep =
    rawNextStep && !isStaleBoilerplateMissionTask(rawNextStep) ? rawNextStep : null;
  const aiStackHealth = useMemo(
    () =>
      resolveAiStackHealth({
        llmConnected: workerStatus?.llmConnected ?? false,
        buildWorker: (workerStatus?.buildWorker as 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE') ?? 'NONE',
        githubConnected: workerStatus?.githubConnected ?? buildRoom?.githubConnected ?? false,
      }),
    [workerStatus, buildRoom],
  );

  const smartChips = useMemo(() => {
    const conn = connectionSnapshotFromSync(syncStatus?.platforms ?? [], {
      llmConnected: workerStatus?.llmConnected ?? false,
      cursorConnected: workerStatus?.connections?.cursor ?? false,
      founderNodeConnected: workerStatus?.connections?.founderNode ?? false,
      repoFullName: memory?.repoFullName ?? buildRoom?.repoFullName,
    });
    return resolveSmartQuickPrompts(conn);
  }, [syncStatus, workerStatus, memory, buildRoom]);

  const showMissionControl = activeTab === 'activity' && !tabContent;

  useEffect(() => {
    if (!showMissionControl) return;
    const today = new Date().toISOString().slice(0, 10);
    try {
      if (sessionStorage.getItem(EXECUTIVE_BRIEF_DATE_KEY) === today) return;
      const raw = sessionStorage.getItem('dcf-copilot-chat-v1');
      if (raw && (JSON.parse(raw) as unknown[]).length > 0) return;
    } catch {
      /* ignore */
    }

    void fetchCopilotStandup(accessToken)
      .then((res) => {
        const text = res.brief?.trim() || res.standup?.trim();
        if (!text) return;
        setExecutiveBrief(text);
        sessionStorage.setItem(EXECUTIVE_BRIEF_DATE_KEY, today);
      })
      .catch(() => {});
  }, [showMissionControl, accessToken]);

  useEffect(() => {
    if (!showMissionControl) return;
    const poll = () => {
      void fetchChiefOfStaffNudges(accessToken)
        .then((res) => setLiveNudges(res.nudges ?? []))
        .catch(() => undefined);
    };
    poll();
    const id = window.setInterval(poll, 90_000);
    return () => window.clearInterval(id);
  }, [showMissionControl, accessToken]);

  const contentDraftReady = useMemo(
    () => founderQueue.some((item) => item.kind === 'PUBLISH_UPDATE'),
    [founderQueue],
  );

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#07070a]">
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
              <p className="mt-3 text-[10px] text-zinc-600">
                {missionIntel && memory?.repoFullName ? 'Current initiative (GitHub)' : 'Current goal'}
              </p>
              <p className="mt-0.5 text-xs leading-snug text-zinc-300 line-clamp-4">{displayInitiative}</p>
              {displayNextStep && (
                <>
                  <p className="mt-2 text-[10px] text-zinc-600">Start here</p>
                  <p className="mt-0.5 text-[11px] leading-snug text-zinc-400 line-clamp-3">
                    {displayNextStep}
                  </p>
                </>
              )}
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

        <main
          className={`min-w-0 flex-1 ${
            showMissionControl
              ? 'flex flex-col overflow-hidden p-3 sm:p-4 lg:p-5'
              : 'overflow-y-auto p-4 sm:p-6 lg:p-8'
          }`}
        >
          {showMissionControl ? (
            <div className="flex min-h-0 flex-1 flex-col gap-3">
              <details className="group shrink-0 rounded-lg border border-zinc-800/80 bg-zinc-900/30">
                <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-medium text-zinc-500 marker:content-none [&::-webkit-details-marker]:hidden">
                  <span className="text-zinc-400">Setup guide & connections</span>
                  <span className="ml-2 text-zinc-600">— optional · expand when connecting GitHub or Cursor</span>
                </summary>
                <div className="space-y-2 border-t border-zinc-800/60 px-3 pb-3 pt-2">
                  <FounderPersonalGuide
                    accessToken={accessToken}
                    compact
                    onPrompt={(p) => {
                      setQuickPrompt(p);
                      setChatKey((k) => k + 1);
                    }}
                  />
                  <MissionControlConnectionHub
                    accessToken={accessToken}
                    providers={aiProviders}
                    defaultProvider={defaultAiProvider}
                    buildWorker={workerStatus?.buildWorker}
                    workerConnections={{
                      cursor: workerStatus?.connections?.cursor,
                      openHands: workerStatus?.connections?.openHands,
                    }}
                    builderWorking={isAgentRunActive(activeAgentRun)}
                    contentDraftReady={contentDraftReady}
                    promo={promoStatus}
                    compact
                    hideWorkforceAgents
                    onRefresh={() => {
                      void load();
                      onRefresh();
                    }}
                  />
                </div>
              </details>

              <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row">
                <div className="flex min-h-0 min-w-0 flex-[1_1_78%] flex-col gap-2">
                  <FounderCopilotChat
                    key={chatKey}
                    accessToken={accessToken}
                    variant="hero"
                    className="min-h-0 flex-1"
                    memory={memory}
                    missionInitiative={displayInitiative}
                    missionNextStep={displayNextStep}
                    defaultSendMode="ask"
                    initialPrompt={quickPrompt}
                    seedAssistantMessage={resumeBriefing ?? executiveBrief}
                    onSeedAssistantConsumed={() => {
                      setResumeBriefing(null);
                      setExecutiveBrief(null);
                    }}
                    activeAgentRunActive={isAgentRunActive(activeAgentRun)}
                    contentDraftReady={contentDraftReady}
                    liveNudges={liveNudges}
                    syncPlatforms={syncStatus?.platforms ?? []}
                    founderNodeConnected={workerStatus?.connections?.founderNode ?? false}
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
                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {smartChips.map((chip) => (
                      <button
                        key={chip.id}
                        type="button"
                        onClick={() => setQuickPrompt(chip.prompt)}
                        className="rounded-full border border-zinc-700/80 bg-zinc-900/50 px-2.5 py-1 text-[10px] text-zinc-300 transition hover:border-violet-500/50 hover:text-white"
                      >
                        {chip.label}
                      </button>
                    ))}
                  </div>
                </div>

                <aside className="flex min-h-0 w-full shrink-0 flex-col gap-2 overflow-y-auto lg:max-w-[17rem] lg:flex-[0_0_22%]">
                  {activeAgentRun && isAgentRunActive(activeAgentRun) && (
                    <AgentRunStepsPanel run={activeAgentRun} />
                  )}
                  <FounderCommandCenterPanels
                    queue={founderQueue}
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
                    <div className="rounded-xl border border-violet-500/25 bg-violet-950/15 p-3 text-xs text-zinc-200">
                      <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300">
                        Mission intel
                      </p>
                      <p className="mt-1 font-medium text-white line-clamp-2">{missionIntel.currentInitiative}</p>
                      <p className="mt-1 text-[10px] text-emerald-200/90 line-clamp-2">
                        Next: {missionIntel.recommendedNextStep}
                      </p>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-1.5">
                    {[
                      { label: 'Tasks', value: String(openTasks) },
                      { label: 'Commits', value: String(buildRoom?.stats.commits ?? 0) },
                      { label: 'Ready', value: `${readiness}%` },
                      {
                        label: 'Agents',
                        value: workerStatus?.buildWorker !== 'NONE' ? '1' : '0',
                      },
                    ].map((stat) => (
                      <div
                        key={stat.label}
                        className="rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-2 py-1.5"
                      >
                        <p className="text-[9px] uppercase tracking-wider text-zinc-600">{stat.label}</p>
                        <p className="text-xs font-semibold text-white">{stat.value}</p>
                      </div>
                    ))}
                  </div>
                  <FounderGraphMiniPanel
                    miniChain={founderGraph?.miniChain ?? []}
                    nodeCount={founderGraph?.nodeCount ?? 0}
                    updatedAt={founderGraph?.updatedAt ?? undefined}
                    loading={commandCenterLoading}
                  />
                </aside>
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

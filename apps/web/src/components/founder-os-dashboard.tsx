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
import { FounderCopilotChat } from '@/components/founder-copilot-chat';
import type { WorkspaceTab } from '@/components/founder-workspace';
import {
  BuildRoomData,
  copilotResume,
  fetchAccountOverview,
  fetchBuildRoom,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  FounderDashboard,
  ProjectMemory,
  ProjectRoom,
} from '@/lib/api';

type NavItem = { id: WorkspaceTab; label: string; icon: string };

const SIDEBAR_NAV: NavItem[] = [
  { id: 'activity', label: 'Mission Control', icon: '◆' },
  { id: 'social', label: 'Social Hub', icon: '📡' },
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'analytics', label: 'Settings', icon: '⚙' },
];

const COPILOT_CHIPS = [
  'What should I ship today?',
  'What broke yesterday?',
  'Continue last task',
  'Create PR',
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
  activeAgentTemplate,
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
  const [resumeBusy, setResumeBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [br, mem, worker, account] = await Promise.all([
        fetchBuildRoom(accessToken),
        fetchCopilotMemory(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
        fetchAccountOverview(accessToken).catch(() => null),
      ]);
      setBuildRoom(br);
      setMemory(mem);
      setWorkerStatus(worker);
      if (account) {
        setUsername(account.username.startsWith('@') ? account.username : `@${account.username}`);
        setAvatarUrl(account.avatarUrl);
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
      void load();
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Resume failed');
    } finally {
      setResumeBusy(false);
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
                <p className="text-[10px] text-zinc-600">Pro Founder</p>
              </div>
            </div>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {showMissionControl ? (
            <div className="mx-auto flex max-w-5xl flex-col gap-6">
              <header className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
                    Mission Control
                  </p>
                  <h1 className="mt-1 text-xl font-bold text-white sm:text-2xl">
                    Run your startup from anywhere
                  </h1>
                </div>
                <button
                  type="button"
                  disabled={resumeBusy}
                  onClick={() => void handleResumeWork()}
                  className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
                >
                  {resumeBusy ? 'Loading…' : '▶ Resume Work'}
                </button>
              </header>

              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { label: 'Open tasks', value: String(openTasks) },
                  { label: 'Active agents', value: workerStatus?.buildWorker !== 'NONE' ? '1' : '0' },
                  {
                    label: 'Commits (7d)',
                    value: String(buildRoom?.stats.commits ?? 0),
                  },
                  {
                    label: 'Next milestone',
                    value: memory?.suggestedNextStep?.slice(0, 24) ?? 'MVP',
                  },
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

              <section className="rounded-2xl border border-violet-500/20 bg-zinc-900/20 p-4 sm:p-5">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                  Current goal
                </p>
                <p className="mt-1 text-lg font-semibold text-white">{currentGoal}</p>
              </section>

              <FounderCopilotChat
                key={chatKey}
                accessToken={accessToken}
                variant="hero"
                memory={memory}
                initialPrompt={quickPrompt}
                agentTemplate={activeAgentTemplate}
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

'use client';

import Link from 'next/link';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import {
  AGENT_CATEGORY_LABELS,
  buildFeedShareMessage,
  buildSiteUrl,
  formatRelativeTime,
  getStageColorLabel,
  getStageColorTheme,
  LIFECYCLE_STAGES,
  STAGE_COLOR_CLASSES,
  WORKFORCE_TEMPLATES,
  buildCopilotAgentDeepLink,
} from '@dcf/utils';
import { FounderCopilotChat } from '@/components/founder-copilot-chat';
import { FounderInboxPanel, useFounderUnreadCount } from '@/components/founder-inbox-panel';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import type { WorkspaceTab } from '@/components/founder-workspace';
import {
  BuildRoomData,
  fetchAccountOverview,
  fetchBuildRoom,
  fetchCopilotMemory,
  fetchFounderOsDashboard,
  FounderDashboard,
  FounderOsDashboard,
  ProjectMemory,
  ProjectRoom,
} from '@/lib/api';

type NavItem =
  | { kind: 'tab'; id: WorkspaceTab; label: string; icon: string }
  | { kind: 'link'; href: string; label: string; icon: string };

const SIDEBAR_NAV: NavItem[] = [
  { kind: 'tab', id: 'activity', label: 'Copilot', icon: '✦' },
  { kind: 'tab', id: 'community', label: 'Projects', icon: '◈' },
  { kind: 'tab', id: 'build', label: 'Memory', icon: '🧠' },
  { kind: 'link', href: '/settings/builder', label: 'Integrations', icon: '⚡' },
  { kind: 'tab', id: 'agents', label: 'Agents', icon: '🤖' },
  { kind: 'tab', id: 'analytics', label: 'Settings', icon: '⚙' },
  { kind: 'tab', id: 'notifications', label: 'Notifications', icon: '🔔' },
];

const PROMPT_CHIPS = [
  'What should I work on today?',
  'Analyze roadmap',
  'Create tokenomics draft',
];

const HOW_IT_WORKS = [
  { step: '1', title: 'You ask', detail: 'Tell Copilot what you need in plain language.' },
  { step: '2', title: 'Copilot understands', detail: 'Project memory, GitHub, and tasks give context.' },
  { step: '3', title: 'Agents get to work', detail: 'Specialized workers draft, research, and ship.' },
  { step: '4', title: 'You get results', detail: 'Updates land here — share wins on X in one click.' },
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

function greetingName(memory: ProjectMemory | null) {
  const welcome = memory?.welcomeMessage ?? 'Welcome back';
  const match = welcome.match(/Welcome back(?:,)?\s+(.+)/i);
  return match?.[1]?.replace(/[.!]$/, '') ?? '@founder';
}

function timeGreeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

type ActivityCard = {
  id: string;
  title: string;
  detail: string;
  when: string;
  tone: 'commit' | 'deploy' | 'update' | 'task';
};

function buildActivityCards(buildRoom: BuildRoomData | null, room: ProjectRoom | null): ActivityCard[] {
  const items: ActivityCard[] = [];
  const commitCount = buildRoom?.stats.commits ?? 0;
  if (commitCount > 0) {
    const latest = buildRoom?.commits[0];
    items.push({
      id: 'commits',
      title: `${commitCount} commit${commitCount === 1 ? '' : 's'} pushed`,
      detail: latest?.message.split('\n')[0].slice(0, 100) ?? 'Recent GitHub activity',
      when: latest ? formatRelativeTime(latest.date) : 'Recently',
      tone: 'commit',
    });
  }
  const openTask = buildRoom?.grouped.tasks.find((t) => t.status !== 'DONE');
  if (openTask) {
    items.push({
      id: `task-${openTask.id}`,
      title: 'Task updated',
      detail: `${openTask.title} · ${openTask.status.replace(/_/g, ' ')}`,
      when: formatRelativeTime(openTask.updatedAt ?? openTask.createdAt),
      tone: 'task',
    });
  }
  const deploy = buildRoom?.deployments[0];
  if (deploy) {
    items.push({
      id: `deploy-${deploy.id}`,
      title: 'Deployment',
      detail: deploy.headline,
      when: formatRelativeTime(deploy.createdAt),
      tone: 'deploy',
    });
  }
  const post = room?.buildPosts?.[0];
  if (post) {
    items.push({
      id: `post-${post.id}`,
      title: 'Build update',
      detail: post.headline,
      when: formatRelativeTime(post.publishedAt),
      tone: 'update',
    });
  }
  if (room?.followerCount && room.followerCount > 0) {
    items.push({
      id: 'community',
      title: 'Community',
      detail: `${room.followerCount} follower${room.followerCount === 1 ? '' : 's'} on your project room`,
      when: 'Live',
      tone: 'update',
    });
  }
  return items.slice(0, 4);
}

function ActivityFeed({ items }: { items: ActivityCard[] }) {
  const origin = useShareOrigin();

  if (items.length === 0) {
    return (
      <p className="text-sm text-zinc-500">Sync GitHub or publish a build update to see activity here.</p>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item) => {
        const shareText = buildFeedShareMessage({ headline: item.title, detail: item.detail });
        const shareUrl = buildSiteUrl(origin, roomPath(item));
        return (
          <article
            key={item.id}
            className="flex flex-col rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4"
          >
            <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-400/80">
              {item.when}
            </p>
            <h4 className="mt-1 text-sm font-semibold text-white">{item.title}</h4>
            <p className="mt-1 flex-1 text-xs leading-relaxed text-zinc-500 line-clamp-3">{item.detail}</p>
            <div className="mt-3">
              <ShareOnXButton text={shareText} url={shareUrl} label="Share on X" className="w-full justify-center" />
            </div>
          </article>
        );
      })}
    </div>
  );
}

function roomPath(item: ActivityCard) {
  if (item.tone === 'commit' || item.tone === 'deploy') return '/founder-den?tab=build';
  if (item.tone === 'task') return '/founder-den?tab=build';
  return '/founder-den';
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
  const [osData, setOsData] = useState<FounderOsDashboard | null>(null);
  const [quickPrompt, setQuickPrompt] = useState<string | null>(initialCopilotPrompt ?? null);
  const [chatKey, setChatKey] = useState(0);
  const [username, setUsername] = useState<string>('@founder');
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const unreadCount = useFounderUnreadCount(accessToken);

  const load = useCallback(async () => {
    try {
      const [br, mem, os, account] = await Promise.all([
        fetchBuildRoom(accessToken),
        fetchCopilotMemory(accessToken),
        fetchFounderOsDashboard(accessToken),
        fetchAccountOverview(accessToken).catch(() => null),
      ]);
      setBuildRoom(br);
      setMemory(mem);
      setOsData(os);
      if (account) {
        setUsername(account.username.startsWith('@') ? account.username : `@${account.username}`);
        setAvatarUrl(account.avatarUrl);
      }
    } catch {
      setBuildRoom(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!initialCopilotPrompt?.trim()) return;
    setQuickPrompt(initialCopilotPrompt);
  }, [initialCopilotPrompt]);

  const stage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';
  const theme = getStageColorTheme(stage, room?.isLiveToken);
  const colors = STAGE_COLOR_CLASSES[theme];
  const progress = memory?.progressPercent ?? room?.launchReadiness ?? dashboard?.launchReadiness ?? 0;
  const readiness = memory?.launchReadiness ?? room?.launchReadiness ?? 0;
  const openTasks = buildRoom?.grouped.tasks.filter((t) => t.status !== 'DONE').length ?? 0;
  const nextMilestone = memory?.suggestedNextStep?.slice(0, 48) ?? 'Define MVP user stories';

  const activityCards = useMemo(() => buildActivityCards(buildRoom, room), [buildRoom, room]);

  const integrations = useMemo(() => {
    const apps = osData?.connectedApps ?? [];
    const byKey = Object.fromEntries(apps.map((a) => [a.provider, a]));
    return [
      { label: 'GitHub', connected: buildRoom?.githubConnected ?? false },
      { label: 'Notion', connected: byKey.notion?.connected ?? false },
      { label: 'Slack', connected: byKey.slack?.connected ?? false },
      { label: 'Telegram', connected: byKey.telegram?.connected ?? false },
      { label: 'X (Twitter)', connected: byKey.x?.connected ?? false },
      { label: 'Vercel', connected: byKey.vercel?.connected ?? false },
    ];
  }, [osData, buildRoom]);

  const llmProviders = useMemo(() => {
    const apps = osData?.connectedApps ?? [];
    const keys = ['deepseek', 'openai', 'anthropic', 'google', 'openrouter'];
    return keys.map((key) => ({
      key,
      label: key === 'anthropic' ? 'Claude' : key === 'google' ? 'Gemini' : key.charAt(0).toUpperCase() + key.slice(1),
      connected: apps.some((a) => a.provider === key && a.connected),
    }));
  }, [osData]);

  const showCopilotHome = activeTab === 'activity' && !tabContent;
  const showNotifications = activeTab === 'notifications';

  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#07070a]">
      <div className="flex flex-1 flex-col lg:flex-row">
        {/* Left sidebar */}
        <aside className="flex w-full shrink-0 flex-col border-b border-zinc-800/80 bg-[#0a0a0e] lg:w-56 lg:border-b-0 lg:border-r xl:w-60">
          <div className="border-b border-zinc-800/60 px-4 py-4">
            <Link href="/" className="text-sm font-bold tracking-tight text-white hover:text-violet-300">
              Doxxed
            </Link>
            <p className="mt-0.5 text-[10px] text-zinc-600">Founder Copilot</p>
          </div>

          <nav className="flex-1 space-y-0.5 p-2">
            {SIDEBAR_NAV.map((item) => {
              if (item.kind === 'link') {
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-zinc-200"
                  >
                    <span className="text-base opacity-80">{item.icon}</span>
                    {item.label}
                  </Link>
                );
              }
              const active = activeTab === item.id;
              const badge = item.id === 'notifications' && unreadCount > 0 ? unreadCount : null;
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
                  <span className="flex-1">{item.label}</span>
                  {badge != null && (
                    <span className="rounded-full bg-violet-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      {badge > 99 ? '99+' : badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          {activeTab !== 'notifications' && (
            <FounderInboxPanel accessToken={accessToken} compact />
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
                <p className="text-[10px] text-zinc-600">Founder</p>
              </div>
            </div>
            <Link
              href="/founder-node"
              className="mt-1 block px-2 text-[10px] text-emerald-500/80 hover:text-emerald-400"
            >
              Founder Node →
            </Link>
          </div>
        </aside>

        {/* Center */}
        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {showNotifications ? (
            <FounderInboxPanel accessToken={accessToken} full />
          ) : showCopilotHome ? (
            <>
              <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
                    Founder Copilot
                  </p>
                  <h1 className="mt-1 text-xl font-bold text-white sm:text-2xl">
                    Your startup&apos;s second brain
                  </h1>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setChatKey((k) => k + 1)}
                    className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:border-violet-500/50 hover:text-white"
                  >
                    + New chat
                  </button>
                  <Link
                    href="/settings/builder"
                    className="rounded-lg border border-violet-500/30 bg-violet-950/30 px-3 py-1.5 text-xs font-medium text-violet-200 hover:bg-violet-950/50"
                  >
                    LLM · Builder
                  </Link>
                </div>
              </header>

              <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { label: 'Launch readiness', value: `${readiness}%` },
                  { label: 'Open tasks', value: String(openTasks) },
                  { label: 'Progress', value: `${progress}%` },
                  { label: 'Next milestone', value: nextMilestone, wide: true },
                ].map((stat) => (
                  <div
                    key={stat.label}
                    className={`rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-3 py-2.5 ${
                      stat.wide ? 'col-span-2 sm:col-span-1' : ''
                    }`}
                  >
                    <p className="text-[10px] uppercase tracking-wider text-zinc-600">{stat.label}</p>
                    <p className="mt-0.5 truncate text-sm font-semibold text-white">{stat.value}</p>
                  </div>
                ))}
              </div>

              <p className="mb-4 text-base text-zinc-300">
                {timeGreeting()}, {greetingName(memory)}. What should we build today?
              </p>

              {activeAgentTemplate && (
                <p className="mb-3 rounded-lg border border-violet-500/30 bg-violet-950/25 px-3 py-2 text-xs text-violet-200">
                  Workforce agent ·{' '}
                  {WORKFORCE_TEMPLATES.find((t) => t.key === activeAgentTemplate)?.label ??
                    activeAgentTemplate.replace(/_/g, ' ')}{' '}
                  — Copilot will route this prompt
                </p>
              )}

              <div className="mb-4 flex flex-wrap gap-2">
                {PROMPT_CHIPS.map((chip) => (
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

              <FounderCopilotChat
                key={chatKey}
                accessToken={accessToken}
                variant="embedded"
                initialPrompt={quickPrompt}
                agentTemplate={activeAgentTemplate}
                onInitialPromptConsumed={() => {
                  setQuickPrompt(null);
                  onInitialCopilotPromptConsumed?.();
                }}
                onResult={(a) => {
                  onMessage?.(a);
                  load();
                  onRefresh();
                }}
              />

              <section className="mt-8">
                <h3 className="text-sm font-semibold text-white">Recent activity</h3>
                <p className="mt-0.5 text-xs text-zinc-500">Share milestones on X in one click</p>
                <div className="mt-4">
                  <ActivityFeed items={activityCards} />
                </div>
              </section>

              <div className="mt-10 grid gap-6 lg:grid-cols-2">
                <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
                  <h3 className="text-sm font-semibold text-white">How Founder Copilot works</h3>
                  <ol className="mt-4 space-y-3">
                    {HOW_IT_WORKS.map((step) => (
                      <li key={step.step} className="flex gap-3">
                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-600/20 text-[10px] font-bold text-violet-300">
                          {step.step}
                        </span>
                        <div>
                          <p className="text-sm font-medium text-zinc-200">{step.title}</p>
                          <p className="text-xs text-zinc-500">{step.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>

                <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-white">Agent ecosystem</h3>
                    <button
                      type="button"
                      onClick={() => onTabChange('agents')}
                      className="text-[10px] text-violet-400 hover:underline"
                    >
                      + Create custom agent
                    </button>
                  </div>
                  <ul className="mt-3 space-y-2">
                    {WORKFORCE_TEMPLATES.slice(0, 5).map((agent) => (
                      <li key={agent.key}>
                        <Link
                          href={buildCopilotAgentDeepLink(agent.key, room?.name)}
                          className="flex items-center justify-between gap-2 rounded-lg border border-zinc-800/60 px-3 py-2 transition hover:border-violet-500/40 hover:bg-violet-950/20"
                        >
                          <div>
                            <p className="text-xs font-medium text-zinc-200">{agent.label}</p>
                            <p className="text-[10px] text-zinc-600">{agent.description}</p>
                          </div>
                          <span className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-[9px] text-zinc-400">
                            {AGENT_CATEGORY_LABELS[agent.category] ?? agent.category}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>

                <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4 lg:col-span-2">
                  <h3 className="text-sm font-semibold text-white">Connecting agents to your stack</h3>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {integrations.map((app) => (
                      <div
                        key={app.label}
                        className="flex items-center justify-between rounded-lg border border-zinc-800/60 px-3 py-2"
                      >
                        <span className="text-sm text-zinc-300">{app.label}</span>
                        {app.connected ? (
                          <span className="text-[10px] font-medium text-emerald-400">✓ Connected</span>
                        ) : (
                          <Link
                            href="/settings/builder"
                            className="rounded border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-400 hover:text-white"
                          >
                            Connect
                          </Link>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            </>
          ) : (
            tabContent ?? (
              <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-6 text-center text-sm text-zinc-500">
                Select a section from the sidebar.
              </div>
            )
          )}
        </main>

        {/* Right sidebar */}
        <aside className="hidden w-72 shrink-0 border-l border-zinc-800/80 bg-[#0a0a0e] p-4 xl:block">
          {room && (
            <section className={`rounded-xl border p-4 ${colors.badge}`}>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                Project context
              </p>
              <Link
                href={`/project/${room.slug}`}
                className="mt-1 block text-lg font-bold text-white hover:text-violet-300"
              >
                {room.name}
              </Link>
              <p className="truncate text-xs text-zinc-500">
                {dashboard?.primaryProjectSlug
                  ? `doxxedcrypto.digital/project/${dashboard.primaryProjectSlug}`
                  : 'doxxedcrypto.digital'}
              </p>
              {memory?.currentGoal && (
                <p className="mt-2 text-xs text-zinc-400">
                  <span className="text-zinc-600">Goal · </span>
                  {memory.currentGoal.slice(0, 80)}
                </p>
              )}
              {buildRoom?.commits[0] && (
                <p className="mt-2 text-[10px] text-zinc-500">
                  <span className="text-zinc-600">Latest commit · </span>
                  {buildRoom.commits[0].message.split('\n')[0].slice(0, 56)}
                </p>
              )}
              <span
                className={`mt-2 inline-block rounded-full border px-2.5 py-0.5 text-[10px] font-semibold ${colors.badge} ${colors.text}`}
              >
                {getStageColorLabel(theme)} · {stageLabel(stage)}
              </span>
            </section>
          )}

          <section className="mt-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
              System architecture
            </h3>
            <div className="mt-3 space-y-1 text-[10px] leading-relaxed text-zinc-500">
              {[
                'You (Founder)',
                '↓',
                'Founder Copilot (Orchestrator)',
                '↓',
                'Agent layer · Researcher, Coder…',
                '↓',
                'Tools · GitHub, X, Vercel…',
                '↓',
                'LLM layer · your API keys',
                '↓',
                'Memory · vectors, docs, tasks',
              ].map((line) => (
                <p key={line} className={line === '↓' ? 'text-center text-zinc-700' : 'text-zinc-400'}>
                  {line}
                </p>
              ))}
            </div>
          </section>

          <section className="mt-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Your own AI</h3>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {llmProviders.map((p) => (
                <span
                  key={p.key}
                  className={`rounded-md px-2 py-1 text-[10px] ${
                    p.connected
                      ? 'border border-violet-500/40 bg-violet-950/30 text-violet-200'
                      : 'border border-zinc-800 text-zinc-600'
                  }`}
                >
                  {p.label}
                </span>
              ))}
            </div>
            <Link href="/settings/builder" className="mt-2 inline-block text-[10px] text-violet-400 hover:underline">
              Manage API keys →
            </Link>
          </section>

          <section className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-950/10 p-4">
            <div className="flex items-center gap-2">
              <span className="text-lg" aria-hidden>
                🛡
              </span>
              <h3 className="text-sm font-semibold text-emerald-200">Private by design</h3>
            </div>
            <ul className="mt-3 space-y-1.5 text-[11px] text-zinc-400">
              <li className="flex gap-2">
                <span className="text-emerald-500">✓</span> End-to-end encryption options
              </li>
              <li className="flex gap-2">
                <span className="text-emerald-500">✓</span> Your keys — not used for training
              </li>
              <li className="flex gap-2">
                <span className="text-emerald-500">✓</span> Founder Node for local vault
              </li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

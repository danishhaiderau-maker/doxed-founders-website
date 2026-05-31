'use client';

import Link from 'next/link';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import {
  formatRelativeTime,
  formatUsd,
  getStageColorLabel,
  getStageColorTheme,
  LIFECYCLE_STAGES,
  STAGE_COLOR_CLASSES,
} from '@dcf/utils';
import { FounderCopilotChat } from '@/components/founder-copilot-chat';
import { FounderInboxPanel } from '@/components/founder-inbox-panel';
import type { WorkspaceTab } from '@/components/founder-workspace';
import {
  BuildRoomData,
  fetchBuildRoom,
  fetchCopilotMemory,
  fetchFounderOsDashboard,
  FounderDashboard,
  FounderOsDashboard,
  ProjectMemory,
  ProjectRoom,
} from '@/lib/api';

const NAV: { id: WorkspaceTab; label: string; icon: string }[] = [
  { id: 'activity', label: 'Mission Control', icon: '◎' },
  { id: 'build', label: 'Founder Copilot', icon: '✦' },
  { id: 'tasks', label: 'Tasks', icon: '☑' },
  { id: 'community', label: 'Community', icon: '👥' },
  { id: 'funding', label: 'Raise Room', icon: '💰' },
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'analytics', label: 'Settings', icon: '⚙' },
];

const DEV_LINKS: { label: string; tab: WorkspaceTab; hint: string }[] = [
  { label: 'Commits', tab: 'build', hint: 'commits' },
  { label: 'Issues', tab: 'build', hint: 'issues' },
  { label: 'Pull Requests', tab: 'build', hint: 'prs' },
  { label: 'Deployments', tab: 'build', hint: 'deployments' },
  { label: 'Files', tab: 'build', hint: 'ideas' },
];

const QUICK_CARDS = [
  { label: 'Most pressing issue', prompt: 'What is the most pressing issue?', icon: '⚡' },
  { label: 'Continue where I left off', prompt: 'Resume work — what should I finish next?', icon: '▶' },
  { label: 'Finish MVP', prompt: 'What is left to finish the MVP?', icon: '🚀' },
  { label: 'Create tokenomics', prompt: 'Create tokenomics draft for community allocation.', icon: '📊' },
  { label: 'Prepare Raise', prompt: 'Prepare launch roadmap for Raise Room.', icon: '💎' },
  { label: 'Weekly update', prompt: "Generate this week's update.", icon: '📝' },
  { label: 'Launch readiness', prompt: 'Create launch readiness report.', icon: '🎯' },
];

export type FounderOsDashboardProps = {
  accessToken: string;
  dashboard: FounderDashboard | null;
  room: ProjectRoom | null;
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
  /** When set, replaces Mission Control center (Copilot + quick cards) for sidebar tabs. */
  tabContent?: ReactNode;
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

function buildInsights(
  buildRoom: BuildRoomData | null,
  memory: ProjectMemory | null,
): { text: string; sub: string; tone: 'good' | 'warn' | 'info' }[] {
  const insights: { text: string; sub: string; tone: 'good' | 'warn' | 'info' }[] = [];
  const commits = buildRoom?.stats.commits ?? 0;
  if (commits > 0) {
    insights.push({
      text: `${commits} commit${commits === 1 ? '' : 's'} synced recently`,
      sub: commits >= 5 ? 'Good momentum' : 'Keep shipping',
      tone: 'good',
    });
  }
  const tasks = buildRoom?.grouped.tasks ?? [];
  const landing = tasks.find((t) => /landing|redesign|ui/i.test(t.title));
  if (landing) {
    insights.push({
      text: 'Landing page redesign in progress',
      sub: 'UI/UX improvements detected',
      tone: 'info',
    });
  }
  const tokenomics = tasks.find((t) => /tokenomics/i.test(t.title));
  if (tokenomics) {
    insights.push({
      text: 'Tokenomics draft task is pending',
      sub: 'High priority — investors are waiting',
      tone: 'warn',
    });
  }
  if (memory?.connectedNodes?.some((n) => n.status === 'online')) {
    insights.push({ text: 'Founder Node is connected', sub: 'Local vault + sync active', tone: 'good' });
  }
  if (insights.length === 0 && memory?.suggestedNextStep) {
    insights.push({
      text: `Focus: ${memory.suggestedNextStep.slice(0, 60)}`,
      sub: 'Suggested from project memory',
      tone: 'info',
    });
  }
  return insights.slice(0, 4);
}

function buildActivity(buildRoom: BuildRoomData | null, room: ProjectRoom | null) {
  const items: { title: string; when: string }[] = [];
  for (const c of buildRoom?.commits.slice(0, 3) ?? []) {
    items.push({ title: c.message.split('\n')[0].slice(0, 80), when: formatRelativeTime(c.date) });
  }
  for (const d of buildRoom?.deployments.slice(0, 2) ?? []) {
    items.push({ title: d.headline, when: formatRelativeTime(d.createdAt) });
  }
  for (const p of room?.buildPosts?.slice(0, 2) ?? []) {
    items.push({ title: p.headline, when: formatRelativeTime(p.publishedAt) });
  }
  return items.slice(0, 5);
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
}: FounderOsDashboardProps) {
  const [buildRoom, setBuildRoom] = useState<BuildRoomData | null>(null);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [osData, setOsData] = useState<FounderOsDashboard | null>(null);
  const [quickPrompt, setQuickPrompt] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [br, mem, os] = await Promise.all([
        fetchBuildRoom(accessToken),
        fetchCopilotMemory(accessToken),
        fetchFounderOsDashboard(accessToken),
      ]);
      setBuildRoom(br);
      setMemory(mem);
      setOsData(os);
    } catch {
      setBuildRoom(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  const stage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';
  const theme = getStageColorTheme(stage, room?.isLiveToken);
  const colors = STAGE_COLOR_CLASSES[theme];
  const progress = memory?.progressPercent ?? room?.launchReadiness ?? dashboard?.launchReadiness ?? 0;
  const readiness = memory?.launchReadiness ?? room?.launchReadiness ?? 0;
  const demand = room?.activeRaise?.totalAllocated ?? dashboard?.simulatedDemand ?? 0;
  const followers = room?.followerCount ?? dashboard?.followers ?? 0;
  const openTasks = buildRoom?.grouped.tasks.filter((t) => t.status !== 'DONE').length ?? 0;
  const openIssues = buildRoom?.grouped.issues.filter((i) => i.status !== 'DONE').length ?? 0;

  const insights = useMemo(() => buildInsights(buildRoom, memory), [buildRoom, memory]);
  const activity = useMemo(() => buildActivity(buildRoom, room), [buildRoom, room]);

  const connectedBuilders = useMemo(() => {
    const apps = osData?.connectedApps ?? [];
    const byKey = Object.fromEntries(apps.map((a) => [a.provider, a]));
    return [
      { label: 'GitHub', connected: buildRoom?.githubConnected ?? false },
      { label: 'Cursor (AI)', connected: buildRoom?.cursorConnected ?? false },
      {
        label: 'Claude Code',
        connected: byKey.anthropic?.connected ?? byKey.claude?.connected ?? false,
      },
      { label: 'Vercel', connected: byKey.vercel?.connected ?? false },
      { label: 'Railway', connected: byKey.railway?.connected ?? false },
      { label: 'Supabase', connected: byKey.supabase?.connected ?? false },
    ];
  }, [osData, buildRoom]);

  const nodeOnline = memory?.connectedNodes?.some((n) => n.status === 'online');
  const showMissionCenter = activeTab === 'activity' && !tabContent;

  return (
    <div className="flex min-h-[calc(100vh-8rem)] flex-col gap-0 overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#07070a]">
      <div className="flex flex-1 flex-col lg:flex-row">
        {/* Left sidebar */}
        <aside className="w-full shrink-0 border-b border-zinc-800/80 bg-[#0a0a0e] lg:w-56 lg:border-b-0 lg:border-r xl:w-60">
          <div className="border-b border-zinc-800/60 px-4 py-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Founder OS</p>
            <p className="mt-0.5 text-xs text-zinc-600">Mission control</p>
          </div>
          <nav className="space-y-0.5 p-2">
            {NAV.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition ${
                  activeTab === item.id
                    ? 'bg-violet-600/20 font-medium text-violet-200'
                    : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
                }`}
              >
                <span className="text-base opacity-80">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </nav>
          <div className="mt-2 border-t border-zinc-800/60 px-3 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Developer view</p>
            <ul className="mt-2 space-y-1">
              {DEV_LINKS.map((link) => (
                <li key={link.label}>
                  <button
                    type="button"
                    onClick={() => onTabChange(link.tab)}
                    className="w-full rounded px-2 py-1.5 text-left text-xs text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300"
                  >
                    {link.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="mx-3 mb-3 mt-2 rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-emerald-300">Founder Node</p>
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                  nodeOnline ? 'bg-emerald-500/20 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
                }`}
              >
                {nodeOnline ? 'Connected' : 'Offline'}
              </span>
            </div>
            <ul className="mt-2 space-y-1 text-[10px] text-zinc-500">
              <li>{nodeOnline ? '✓ Vault synced' : '○ Install Founder Node locally'}</li>
              <li>{memory?.repoFullName ? '✓ Memory loaded' : '○ Connect GitHub repo'}</li>
              <li>{nodeOnline ? '✓ Private mode' : '○ Pair from tray app'}</li>
            </ul>
            <Link
              href="/founder-node"
              className="mt-2 inline-block text-[10px] text-emerald-400 hover:underline"
            >
              Open Founder Node →
            </Link>
          </div>
          <FounderInboxPanel accessToken={accessToken} compact />
        </aside>

        {/* Center */}
        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {showMissionCenter ? (
            <>
          <header className="mb-6">
            <h1 className="text-2xl font-bold text-white sm:text-3xl">
              {timeGreeting()}, {greetingName(memory)}{' '}
              <span className="inline-block" aria-hidden>
                👋
              </span>
            </h1>
            <p className="mt-1 text-sm text-zinc-500">What shall we build today?</p>
          </header>

          <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {QUICK_CARDS.map((card) => (
              <button
                key={card.label}
                type="button"
                onClick={() => setQuickPrompt(card.prompt)}
                className="group flex items-start gap-3 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 text-left transition hover:border-violet-500/40 hover:bg-violet-950/20"
              >
                <span className="text-xl">{card.icon}</span>
                <span className="text-sm font-medium text-zinc-200 group-hover:text-white">{card.label}</span>
              </button>
            ))}
          </div>

          <FounderCopilotChat
            accessToken={accessToken}
            variant="embedded"
            initialPrompt={quickPrompt}
            onInitialPromptConsumed={() => setQuickPrompt(null)}
            onResult={(a) => {
              onMessage?.(a);
              load();
              onRefresh();
            }}
          />

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
              <h3 className="text-sm font-semibold text-white">AI Insights</h3>
              <ul className="mt-3 space-y-3">
                {insights.map((item) => (
                  <li key={item.text} className="flex gap-3">
                    <span
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                        item.tone === 'good'
                          ? 'bg-emerald-500'
                          : item.tone === 'warn'
                            ? 'bg-amber-500'
                            : 'bg-violet-500'
                      }`}
                    />
                    <div>
                      <p className="text-sm text-zinc-200">{item.text}</p>
                      <p className="text-xs text-zinc-500">{item.sub}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <section className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
              <h3 className="text-sm font-semibold text-white">Recent activity</h3>
              <ul className="mt-3 space-y-2.5">
                {activity.length === 0 ? (
                  <li className="text-sm text-zinc-500">Sync GitHub to see commits and deploys here.</li>
                ) : (
                  activity.map((item) => (
                    <li key={`${item.title}-${item.when}`} className="flex justify-between gap-2 text-sm">
                      <span className="truncate text-zinc-300">{item.title}</span>
                      <span className="shrink-0 text-xs text-zinc-600">{item.when}</span>
                    </li>
                  ))
                )}
              </ul>
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
        <aside className="w-full shrink-0 border-t border-zinc-800/80 bg-[#0a0a0e] p-4 lg:w-72 lg:border-l lg:border-t-0 xl:w-80">
          {room && (
            <section className={`rounded-xl border p-4 ${colors.badge}`}>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Project status</p>
              <Link href={`/project/${room.slug}`} className="mt-1 block text-lg font-bold text-white hover:text-violet-300">
                {room.name}
              </Link>
              <p className="truncate text-xs text-zinc-500">
                {dashboard?.primaryProjectSlug
                  ? `doxxedcrypto.digital/project/${dashboard.primaryProjectSlug}`
                  : 'https://doxxedcrypto.digital'}
              </p>
              <span className={`mt-2 inline-block rounded-full border px-2.5 py-0.5 text-[10px] font-semibold ${colors.badge} ${colors.text}`}>
                {getStageColorLabel(theme)} · {stageLabel(stage)}
              </span>
              <div className="mt-4">
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>Launch readiness</span>
                  <span className="font-semibold text-white">{readiness}/100</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className="h-full rounded-full bg-violet-500 transition-all"
                    style={{ width: `${Math.min(100, readiness)}%` }}
                  />
                </div>
              </div>
              <p className="mt-3 text-xs text-zinc-400">
                <span className="text-zinc-600">Next milestone · </span>
                {memory?.suggestedNextStep?.slice(0, 72) ?? 'Define MVP user stories'}
              </p>
            </section>
          )}

          <section className="mt-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
            <h3 className="text-sm font-semibold text-white">Connected builders</h3>
            <ul className="mt-3 space-y-2">
              {connectedBuilders.map((b) => (
                <li key={b.label} className="flex items-center justify-between text-sm">
                  <span className="text-zinc-400">{b.label}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      b.connected ? 'bg-emerald-500/15 text-emerald-400' : 'bg-zinc-800 text-zinc-600'
                    }`}
                  >
                    {b.connected ? 'Connected' : 'Not connected'}
                  </span>
                </li>
              ))}
            </ul>
            <Link
              href="/settings/builder"
              className="mt-3 inline-block text-xs text-violet-400 hover:underline"
            >
              Manage integrations →
            </Link>
          </section>
        </aside>
      </div>

      {/* Bottom stats bar */}
      <footer className="grid grid-cols-2 gap-px border-t border-zinc-800/80 bg-zinc-800/80 sm:grid-cols-3 lg:grid-cols-6">
        {[
          { label: 'Commits', value: String(buildRoom?.stats.commits ?? 0), sub: 'synced' },
          { label: 'Open tasks', value: String(openTasks), sub: 'in queue' },
          { label: 'Issues', value: String(openIssues), sub: 'tracked' },
          { label: 'Followers', value: String(followers), sub: 'total' },
          { label: 'Raise interest', value: formatUsd(demand, 0), sub: 'Ddollar' },
          { label: 'Progress', value: `${progress}%`, sub: 'project' },
        ].map((stat) => (
          <div key={stat.label} className="bg-[#07070a] px-4 py-3">
            <p className="text-[10px] uppercase tracking-wider text-zinc-600">{stat.label}</p>
            <p className="text-lg font-bold text-white">{stat.value}</p>
            <p className="text-[10px] text-zinc-600">{stat.sub}</p>
          </div>
        ))}
      </footer>
    </div>
  );
}

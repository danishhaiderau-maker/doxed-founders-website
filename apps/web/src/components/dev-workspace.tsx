'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { WorkspaceActivity } from '@dcf/utils';
import {
  fetchWorkspaceActivity,
  fetchBuilderWorkerStatus,
  fetchBuilderSettings,
  fetchDeployIntelligence,
  fetchDesktopBridge,
  fetchActiveAgentRun,
  fetchFounderOnboardingStatus,
  type BuilderSettings,
  type DeployIntelligenceResponse,
  type DesktopBridgeResponse,
  type FounderAgentRunRecord,
  type FounderOnboardingStatus,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

type WorkerStatus = {
  buildWorker: string;
  connections: { cursor: boolean; openHands: boolean; founderNode: boolean };
  llmConnected: boolean;
  githubConnected: boolean;
  cursorAgentUrl: string | null;
  latestRunId: string | null;
  cursorAgentId?: string | null;
};

type ModelInfo = {
  key: string;
  label: string;
  provider: string;
  model: string;
  contextWindow: string;
  costPerMtokens: string;
  strengths: string;
  connected: boolean;
  promo: boolean;
  promoLabel?: string;
};

const ALL_MODELS: Omit<ModelInfo, 'connected' | 'promo'>[] = [
  { key: 'GLM', label: 'GLM 5.2', provider: 'ZhipuAI', model: 'glm-5.2', contextWindow: '128K', costPerMtokens: '$0.14', strengths: 'Coding · Planning · Cheap' },
  { key: 'ANTHROPIC', label: 'Claude 4 Sonnet', provider: 'Anthropic', model: 'claude-sonnet-4-20250514', contextWindow: '200K', costPerMtokens: '$3.00', strengths: 'Coding · Review · Reasoning' },
  { key: 'OPENAI', label: 'GPT-5', provider: 'OpenAI', model: 'gpt-5', contextWindow: '256K', costPerMtokens: '$5.00', strengths: 'Reasoning · General · Fast' },
  { key: 'OPENAI_THINKING', label: 'GPT-5 Thinking', provider: 'OpenAI', model: 'gpt-5-thinking', contextWindow: '256K', costPerMtokens: '$8.00', strengths: 'Deep reasoning · Complex tasks' },
  { key: 'GEMINI', label: 'Gemini 2.5 Pro', provider: 'Google', model: 'gemini-2.5-pro', contextWindow: '1M', costPerMtokens: '$1.25', strengths: 'Long context · Multimodal' },
  { key: 'DEEPSEEK', label: 'DeepSeek V3', provider: 'DeepSeek', model: 'deepseek-chat', contextWindow: '64K', costPerMtokens: '$0.14', strengths: 'Coding · Planning · Cheap' },
  { key: 'GROK', label: 'Grok 4', provider: 'xAI', model: 'grok-4', contextWindow: '128K', costPerMtokens: '$5.00', strengths: 'Real-time · Coding' },
  { key: 'CURSOR', label: 'Cursor Agent', provider: 'Cursor', model: 'cursor-agent', contextWindow: '200K', costPerMtokens: '$20/mo', strengths: 'Autonomous coding · PR creation' },
  { key: 'OPENROUTER', label: 'OpenRouter', provider: 'OpenRouter', model: 'auto', contextWindow: 'varies', costPerMtokens: 'varies', strengths: 'Multi-model routing' },
  { key: 'OLLAMA', label: 'Local Ollama', provider: 'Self-hosted', model: 'local', contextWindow: 'varies', costPerMtokens: 'Free', strengths: 'Private · Offline · Zero cost' },
];

const NAV_ITEMS = [
  { id: 'workspace', label: 'Workspace', icon: '▦' },
  { id: 'repositories', label: 'Repositories', icon: '◇' },
  { id: 'agents', label: 'AI Agents', icon: '◉' },
  { id: 'timeline', label: 'Timeline', icon: '≡' },
  { id: 'deployments', label: 'Deployments', icon: '↑' },
  { id: 'terminal', label: 'Terminal', icon: '>' },
  { id: 'databases', label: 'Databases', icon: '◍' },
  { id: 'mcp', label: 'MCP Servers', icon: '⬡' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
] as const;

export function DevWorkspace({ accessToken }: Props) {
  const [activity, setActivity] = useState<WorkspaceActivity | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [settings, setSettings] = useState<BuilderSettings | null>(null);
  const [deploys, setDeploys] = useState<DeployIntelligenceResponse | null>(null);
  const [bridge, setBridge] = useState<DesktopBridgeResponse | null>(null);
  const [activeRun, setActiveRun] = useState<{ run: FounderAgentRunRecord | null; active: boolean } | null>(null);
  const [onboarding, setOnboarding] = useState<FounderOnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null);
  const [activeNav, setActiveNav] = useState<string>('workspace');
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'agent'; text: string; model?: string }[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        fetchWorkspaceActivity(accessToken),
        fetchBuilderWorkerStatus(accessToken),
        fetchBuilderSettings(accessToken),
        fetchDeployIntelligence(accessToken),
        fetchDesktopBridge(accessToken),
        fetchActiveAgentRun(accessToken),
        fetchFounderOnboardingStatus(accessToken),
      ]);
      if (results[0].status === 'fulfilled') setActivity(results[0].value);
      if (results[1].status === 'fulfilled') setWorker(results[1].value);
      if (results[2].status === 'fulfilled') setSettings(results[2].value);
      if (results[3].status === 'fulfilled') setDeploys(results[3].value);
      if (results[4].status === 'fulfilled') setBridge(results[4].value);
      if (results[5].status === 'fulfilled') setActiveRun(results[5].value);
      if (results[6].status === 'fulfilled') setOnboarding(results[6].value);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15_000);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  const models = useMemo<ModelInfo[]>(() => {
    const connectedProviders = new Set<string>();
    if (settings?.defaultBrainConnected) connectedProviders.add(settings.defaultProvider);
    if (settings?.secretsStatus?.credentials) {
      for (const c of settings.secretsStatus.credentials) {
        if (c.connected) connectedProviders.add(c.provider.toUpperCase());
      }
    }
    if (worker?.connections?.cursor) connectedProviders.add('CURSOR');

    const promoEligible = onboarding?.promo?.eligible && onboarding?.promo?.hasLlm;

    return ALL_MODELS.map((m) => ({
      ...m,
      connected: connectedProviders.has(m.key),
      promo: Boolean(m.key === 'GLM' && promoEligible),
      promoLabel: m.key === 'GLM' && promoEligible ? 'Doxxed Crypto Promo' : undefined,
    })).sort((a, b) => {
      if (a.promo && !b.promo) return -1;
      if (!a.promo && b.promo) return 1;
      if (a.connected && !b.connected) return -1;
      if (!a.connected && b.connected) return 1;
      return 0;
    });
  }, [settings, worker, onboarding]);

  useEffect(() => {
    if (!selectedModel && models.length > 0) {
      const first = models.find((m) => m.promo || m.connected) ?? models[0];
      setSelectedModel(first);
    }
  }, [models, selectedModel]);

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading workspace…</div>;
  }

  const repo = activity?.repoFullName ?? settings?.repoFullName ?? null;
  const branch = bridge?.latest?.branch ?? activity?.defaultBranch ?? 'main';
  const recentCommits = (activity?.commitsLast2h ?? activity?.commitsLast24h ?? []).slice(0, 10);
  const lastDeploy = deploys?.cards?.[0] ?? null;
  const runActive = activeRun?.active && activeRun.run;
  const promoActive = onboarding?.promo?.eligible && onboarding?.promo?.hasLlm;

  function sendChat() {
    const text = chatInput.trim();
    if (!text) return;
    setChatMessages((prev) => [...prev, { role: 'user', text, model: selectedModel?.label }]);
    setChatInput('');
    // Simulate agent response (real integration via /copilot/ask happens elsewhere)
    const repoInfo = repo ? ` in ${repo}` : '';
    const agentStatus = runActive ? 'An agent is already running — I will queue this after it completes.' : 'Ready to dispatch.';
    setTimeout(() => {
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'agent',
          text: `Analyzing with ${selectedModel?.label ?? 'AI'}… I can see you are on branch ${branch}${repoInfo}. ${agentStatus}`,
          model: selectedModel?.label,
        },
      ]);
    }, 800);
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0a0a0f] text-zinc-100">
      {/* ═══ Top Nav Bar ═══ */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-zinc-800/80 bg-[#0d0d14] px-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold tracking-tight text-white">FOUNDER OS</span>
            <span className="hidden text-[10px] text-zinc-500 sm:inline">Development Workspace</span>
          </div>
          {repo && (
            <div className="flex items-center gap-1.5 text-xs text-zinc-400">
              <span className="font-mono text-zinc-300">{repo}</span>
              <span className="text-zinc-700">/</span>
              <span className="inline-flex items-center gap-1 rounded-md bg-zinc-800/50 px-1.5 py-0.5 font-mono text-violet-300">
                <BranchIcon /> {branch}
              </span>
              <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Up to date
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500">
            + New Agent
          </button>
          <div className="flex items-center gap-2 text-zinc-500">
            <IconBtn label="Search" />
            <IconBtn label="Notifications" />
            <IconBtn label="Settings" />
            <div className="h-6 w-6 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600" />
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* ═══ Left Sidebar ═══ */}
        <aside className="hidden w-52 shrink-0 flex-col border-r border-zinc-800/80 bg-[#0d0d14] md:flex">
          <nav className="flex-1 overflow-y-auto p-2">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                onClick={() => setActiveNav(item.id)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition ${
                  activeNav === item.id
                    ? 'bg-violet-600/15 text-violet-300'
                    : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
                }`}
              >
                <span className="w-4 text-center text-sm opacity-70">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </nav>

          {/* System Status */}
          <div className="border-t border-zinc-800/80 p-3">
            <p className="mb-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">System Status</p>
            <div className="flex items-center gap-1.5 text-[10px] text-emerald-400">
              <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
              All Systems Operational
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              <ConnCard label="Cursor" ok={worker?.connections?.cursor ?? false} />
              <ConnCard label="GitHub" ok={worker?.githubConnected ?? false} />
              <ConnCard label="Railway" ok={Boolean(lastDeploy)} />
              <ConnCard label="Neon DB" ok={worker?.llmConnected ?? false} />
              <ConnCard label="Vercel" ok={Boolean(lastDeploy)} />
              <ConnCard label="Node" ok={worker?.connections?.founderNode ?? false} />
            </div>
          </div>

          {/* User profile */}
          <div className="border-t border-zinc-800/80 p-3">
            <div className="flex items-center gap-2">
              <div className="h-7 w-7 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600" />
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-zinc-200">Danish Haider</p>
                <p className="truncate text-[9px] text-zinc-600">Founder</p>
              </div>
            </div>
          </div>
        </aside>

        {/* ═══ Main Content ═══ */}
        <main className="flex flex-1 flex-col overflow-hidden">
          {/* Project Health Bar */}
          <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-zinc-800/80 bg-[#0d0d14] px-3 py-2">
            <HealthCard label="Build" value="Passing" ok />
            <HealthCard label="Tests" value="93%" sub="248/267" />
            <HealthCard label="Coverage" value="78%" />
            <HealthCard label="Deploy" value={lastDeploy ? 'Live' : 'None'} ok={Boolean(lastDeploy)} />
            <HealthCard label="Env" value="Production" />
            <HealthCard label="Cursor" value={worker?.connections?.cursor ? 'Connected' : 'Offline'} ok={worker?.connections?.cursor ?? false} />
            <HealthCard label="Stack" value="Next.js 15" sub="Node 22" />
          </div>

          {/* Commit Timeline */}
          <div className="shrink-0 border-b border-zinc-800/80 bg-[#0d0d14] px-4 py-2.5">
            <div className="flex items-center gap-1">
              <span className="mr-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Commits</span>
              {recentCommits.length > 0 ? (
                <div className="flex flex-1 items-center gap-0.5 overflow-x-auto">
                  {recentCommits.map((c, i) => (
                    <div key={c.sha} className="group relative flex items-center">
                      {i > 0 && <div className="h-px w-4 bg-zinc-700" />}
                      <button className="h-2.5 w-2.5 shrink-0 rounded-full bg-violet-500 ring-2 ring-violet-500/20 transition hover:ring-violet-400/40" title={c.message} />
                      <div className="pointer-events-none absolute bottom-5 left-1/2 z-20 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300 group-hover:block">
                        <span className="font-mono text-violet-400">{c.sha.slice(0, 7)}</span> · {c.message.split('\n')[0].slice(0, 50)}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <span className="text-[10px] text-zinc-600">No recent commits</span>
              )}
            </div>
          </div>

          {/* Center: Agent Chat + Model Selector */}
          <div className="flex flex-1 overflow-hidden">
            {/* Chat area */}
            <div className="flex flex-1 flex-col overflow-hidden">
              {/* Model selector bar */}
              <div className="relative flex shrink-0 items-center justify-between border-b border-zinc-800/60 px-4 py-2">
                <button
                  onClick={() => setModelDropdownOpen((v) => !v)}
                  className="flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-violet-500/50"
                >
                  <span className={`h-2 w-2 rounded-full ${selectedModel?.promo ? 'bg-amber-400' : selectedModel?.connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
                  {selectedModel?.label ?? 'Select AI'}
                  <span className="text-[9px] text-zinc-500">{selectedModel?.provider}</span>
                  <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" /></svg>
                </button>

                {/* AI Model Dropdown */}
                {modelDropdownOpen && (
                  <div className="absolute left-4 top-11 z-30 w-80 rounded-xl border border-zinc-700 bg-[#12121a] shadow-2xl">
                    <div className="border-b border-zinc-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                      AI Models — Connected & Promo first
                    </div>
                    <div className="max-h-80 overflow-y-auto py-1">
                      {models.map((m) => (
                        <button
                          key={m.key}
                          onClick={() => {
                            setSelectedModel(m);
                            setModelDropdownOpen(false);
                          }}
                          className={`flex w-full items-start gap-2.5 px-3 py-2 text-left transition hover:bg-zinc-800/50 ${
                            selectedModel?.key === m.key ? 'bg-violet-600/10' : ''
                          }`}
                        >
                          <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                            m.promo ? 'bg-amber-400' : m.connected ? 'bg-emerald-400' : 'bg-zinc-600'
                          }`} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs font-medium text-zinc-200">{m.label}</span>
                              {m.promo && (
                                <span className="rounded bg-amber-500/15 px-1 py-0.5 text-[8px] font-semibold text-amber-300">
                                  {m.promoLabel}
                                </span>
                              )}
                              {m.connected && !m.promo && (
                                <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[8px] font-semibold text-emerald-300">
                                  Connected
                                </span>
                              )}
                              {selectedModel?.key === m.key && (
                                <span className="ml-auto text-violet-400">✓</span>
                              )}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2 text-[9px] text-zinc-500">
                              <span>{m.provider}</span>
                              <span>·</span>
                              <span>{m.contextWindow} ctx</span>
                              <span>·</span>
                              <span>{m.costPerMtokens}/M</span>
                            </div>
                            <p className="mt-0.5 text-[9px] text-zinc-600">{m.strengths}</p>
                          </div>
                        </button>
                      ))}
                    </div>
                    <div className="border-t border-zinc-800 px-3 py-2 text-[9px] text-zinc-600">
                      Connect your own API keys in Settings → Builder for more models
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                  {promoActive && (
                    <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[9px] font-semibold text-amber-300">
                      Doxxed Crypto Promo Active
                    </span>
                  )}
                  <span>{selectedModel?.contextWindow} context</span>
                </div>
              </div>

              {/* Chat messages */}
              <div className="flex-1 overflow-y-auto px-4 py-3">
                {chatMessages.length === 0 ? (
                  <div className="flex h-full flex-col items-center justify-center text-center">
                    <p className="text-sm text-zinc-400">
                      Founder Brain knows your repo, branch, commits, and active agents.
                    </p>
                    <p className="mt-1 text-xs text-zinc-600">
                      Type a command — "fix it", "add tests", "deploy", "review PR"
                    </p>
                    {runActive && (
                      <div className="mt-4 rounded-lg border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs text-violet-200">
                        Agent active: {runActive.task.slice(0, 60)}…
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-3">
                    {chatMessages.map((msg, i) => (
                      <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                        <div
                          className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${
                            msg.role === 'user'
                              ? 'bg-violet-600 text-white'
                              : 'bg-zinc-800 text-zinc-200'
                          }`}
                        >
                          {msg.model && (
                            <p className="mb-1 text-[9px] font-semibold opacity-60">{msg.model}</p>
                          )}
                          <p>{msg.text}</p>
                        </div>
                      </div>
                    ))}
                    <div ref={chatEndRef} />
                  </div>
                )}
              </div>

              {/* Chat input */}
              <div className="shrink-0 border-t border-zinc-800/60 p-3">
                <div className="flex items-end gap-2">
                  <textarea
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        sendChat();
                      }
                    }}
                    placeholder="Ask Founder Brain… (it knows your repo, branch, and agents)"
                    rows={1}
                    className="flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-500/50 focus:outline-none"
                  />
                  <button
                    onClick={sendChat}
                    disabled={!chatInput.trim()}
                    className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-30"
                  >
                    Send
                  </button>
                </div>
              </div>
            </div>

            {/* ═══ Right Sidebar: Activity Feed + Integrations ═══ */}
            <aside className="hidden w-64 shrink-0 flex-col border-l border-zinc-800/80 bg-[#0d0d14] lg:flex">
              {/* Activity Feed */}
              <div className="flex-1 overflow-y-auto p-3">
                <p className="mb-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Activity Feed</p>
                <div className="space-y-2">
                  {recentCommits.slice(0, 6).map((c) => (
                    <ActivityItem
                      key={c.sha}
                      time={c.date}
                      icon="◆"
                      iconColor="text-violet-400"
                      title="GitHub"
                      body={c.message.split('\n')[0].slice(0, 50)}
                    />
                  ))}
                  {lastDeploy && (
                    <ActivityItem
                      time={lastDeploy.at}
                      icon="↑"
                      iconColor="text-emerald-400"
                      title="Railway"
                      body={lastDeploy.title.slice(0, 50)}
                    />
                  )}
                  {runActive && (
                    <ActivityItem
                      time={runActive.startedAt}
                      icon="◉"
                      iconColor="text-violet-400"
                      title={runActive.worker ?? 'Agent'}
                      body={runActive.task.slice(0, 50)}
                    />
                  )}
                  {recentCommits.length === 0 && !lastDeploy && !runActive && (
                    <p className="text-[10px] text-zinc-600">No recent activity</p>
                  )}
                </div>
              </div>

              {/* Integrations */}
              <div className="border-t border-zinc-800/80 p-3">
                <p className="mb-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Integrations</p>
                <div className="space-y-1.5">
                  <IntegrationRow label="Cursor IDE" ok={worker?.connections?.cursor ?? false} />
                  <IntegrationRow label="GitHub" ok={worker?.githubConnected ?? false} />
                  <IntegrationRow label="Railway" ok={Boolean(lastDeploy)} />
                  <IntegrationRow label="Neon" ok={worker?.llmConnected ?? false} />
                  <IntegrationRow label="Vercel" ok={Boolean(lastDeploy)} />
                  <IntegrationRow label="Founder Node" ok={worker?.connections?.founderNode ?? false} />
                </div>
                <button className="mt-2 w-full text-center text-[10px] text-violet-400 hover:underline">
                  Manage Integrations →
                </button>
              </div>
            </aside>
          </div>

          {/* ═══ Bottom: Active Agents ═══ */}
          <div className="h-32 shrink-0 border-t border-zinc-800/80 bg-[#0d0d14]">
            <div className="flex h-full">
              {/* Terminal preview */}
              <div className="hidden flex-1 flex-col border-r border-zinc-800/60 sm:flex">
                <div className="flex items-center gap-1.5 border-b border-zinc-800/60 px-3 py-1">
                  <span className="h-2 w-2 rounded-full bg-red-500/60" />
                  <span className="h-2 w-2 rounded-full bg-amber-500/60" />
                  <span className="h-2 w-2 rounded-full bg-emerald-500/60" />
                  <span className="ml-1.5 text-[9px] text-zinc-600">Terminal</span>
                </div>
                <div className="flex-1 overflow-y-auto px-3 py-1.5 font-mono text-[10px] text-emerald-400/70">
                  <p>$ git status</p>
                  <p className="text-zinc-600">On branch {branch}</p>
                  <p className="text-zinc-600">nothing to commit, working tree clean</p>
                  {runActive && <p className="text-violet-400">$ agent: {runActive.status} — {runActive.task.slice(0, 40)}</p>}
                  <p className="text-zinc-500">$ <span className="animate-pulse">█</span></p>
                </div>
              </div>

              {/* Active Agents */}
              <div className="flex flex-1 flex-col overflow-hidden">
                <div className="border-b border-zinc-800/60 px-3 py-1 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
                  Active Agents
                </div>
                <div className="flex-1 overflow-y-auto p-2">
                  {runActive ? (
                    <AgentCard
                      name={runActive.worker ?? 'Agent'}
                      model={runActive.adapterLabel ?? 'cursor'}
                      task={runActive.task}
                      file={runActive.branch ?? ''}
                      progress={65}
                      status={runActive.status}
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-[10px] text-zinc-600">
                      No agents running — click "+ New Agent" to dispatch
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/* ───── Sub-components ───── */

function HealthCard({ label, value, sub, ok }: { label: string; value: string; sub?: string; ok?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
      <div>
        <p className="text-[9px] uppercase tracking-wider text-zinc-600">{label}</p>
        <p className={`text-xs font-semibold ${ok ? 'text-emerald-300' : 'text-zinc-200'}`}>{value}</p>
      </div>
      {sub && <span className="text-[9px] text-zinc-600">{sub}</span>}
    </div>
  );
}

function ConnCard({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[9px] ${
      ok ? 'border-emerald-500/20 bg-emerald-950/20 text-emerald-300' : 'border-zinc-800 bg-zinc-900/30 text-zinc-600'
    }`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
      {label}
    </div>
  );
}

function IntegrationRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between text-[10px]">
      <span className="text-zinc-400">{label}</span>
      <span className={`font-medium ${ok ? 'text-emerald-300' : 'text-zinc-600'}`}>
        {ok ? 'Connected' : 'Offline'}
      </span>
    </div>
  );
}

function ActivityItem({ time, icon, iconColor, title, body }: { time: string; icon: string; iconColor: string; title: string; body: string }) {
  return (
    <div className="flex gap-2">
      <span className={`mt-0.5 text-xs ${iconColor}`}>{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-medium text-zinc-300">{title}</span>
          <span className="text-[8px] text-zinc-600">{new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        </div>
        <p className="truncate text-[9px] text-zinc-500">{body}</p>
      </div>
    </div>
  );
}

function AgentCard({ name, model, task, file, progress, status }: { name: string; model: string; task: string; file: string; progress: number; status: string }) {
  return (
    <div className="rounded-lg border border-violet-500/20 bg-violet-950/10 p-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-violet-400" />
          <span className="text-[10px] font-semibold text-violet-200">{name}</span>
          <span className="text-[8px] text-violet-400/60">{model}</span>
        </div>
        <span className="text-[8px] text-zinc-500">{status}</span>
      </div>
      <p className="mt-1 truncate text-[9px] text-zinc-400">{task}</p>
      {file && <p className="mt-0.5 truncate font-mono text-[8px] text-violet-400/60">{file}</p>}
      <div className="mt-1.5 flex items-center gap-2">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full rounded-full bg-violet-500" style={{ width: `${progress}%` }} />
        </div>
        <span className="text-[8px] text-zinc-600">{progress}%</span>
        <button className="text-[8px] text-red-400 hover:text-red-300">Stop</button>
      </div>
    </div>
  );
}

function IconBtn({ label }: { label: string }) {
  return (
    <button className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300" title={label}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
        <circle cx="8" cy="8" r="1.5" />
      </svg>
    </button>
  );
}

function BranchIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" fill="none">
      <path d="M5 4.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm9 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM3.5 6v4a2.5 2.5 0 0 0 2.5 2.5h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

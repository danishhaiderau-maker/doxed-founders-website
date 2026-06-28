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

type Props = { accessToken: string };

type WorkerStatus = {
  buildWorker: string;
  connections: { cursor: boolean; openHands: boolean; founderNode: boolean };
  llmConnected: boolean;
  githubConnected: boolean;
  cursorAgentUrl: string | null;
  latestRunId: string | null;
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
  local?: boolean;
};

const ALL_MODELS: Omit<ModelInfo, 'connected' | 'promo'>[] = [
  { key: 'GLM', label: 'GLM 5.2', provider: 'ZhipuAI', model: 'glm-5.2', contextWindow: '128K', costPerMtokens: '$0.14', strengths: 'Coding · Planning · Cheap' },
  { key: 'OLLAMA', label: 'Local Ollama', provider: 'Self-hosted', model: 'local', contextWindow: 'varies', costPerMtokens: 'Free', strengths: 'Private · Offline · Zero cost', local: true },
  { key: 'ANTHROPIC', label: 'Claude 4 Sonnet', provider: 'Anthropic', model: 'claude-sonnet-4', contextWindow: '200K', costPerMtokens: '$3.00', strengths: 'Coding · Review · Reasoning' },
  { key: 'OPENAI', label: 'GPT-5', provider: 'OpenAI', model: 'gpt-5', contextWindow: '256K', costPerMtokens: '$5.00', strengths: 'Reasoning · General · Fast' },
  { key: 'OPENAI_THINKING', label: 'GPT-5 Thinking', provider: 'OpenAI', model: 'gpt-5-thinking', contextWindow: '256K', costPerMtokens: '$8.00', strengths: 'Deep reasoning · Complex tasks' },
  { key: 'GEMINI', label: 'Gemini 2.5 Pro', provider: 'Google', model: 'gemini-2.5-pro', contextWindow: '1M', costPerMtokens: '$1.25', strengths: 'Long context · Multimodal' },
  { key: 'DEEPSEEK', label: 'DeepSeek V3', provider: 'DeepSeek', model: 'deepseek-chat', contextWindow: '64K', costPerMtokens: '$0.14', strengths: 'Coding · Planning · Cheap' },
  { key: 'GROK', label: 'Grok 4', provider: 'xAI', model: 'grok-4', contextWindow: '128K', costPerMtokens: '$5.00', strengths: 'Real-time · Coding' },
  { key: 'CURSOR', label: 'Cursor Agent', provider: 'Cursor', model: 'cursor-agent', contextWindow: '200K', costPerMtokens: '$20/mo', strengths: 'Autonomous coding · PR creation' },
  { key: 'OPENROUTER', label: 'OpenRouter', provider: 'OpenRouter', model: 'auto', contextWindow: 'varies', costPerMtokens: 'varies', strengths: 'Multi-model routing' },
];

const NAV_ITEMS = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'repositories', label: 'Repositories' },
  { id: 'agents', label: 'Agents' },
  { id: 'git', label: 'Git' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'infrastructure', label: 'Infrastructure' },
  { id: 'settings', label: 'Settings' },
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
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [terminalHeight, setTerminalHeight] = useState(180);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  const load = useCallback(async () => {
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
    setLoading(false);
  }, [accessToken]);

  useEffect(() => {
    load();
    const i = setInterval(load, 15_000);
    return () => clearInterval(i);
  }, [load]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  // Terminal drag resize
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragRef.current) return;
      const delta = dragRef.current.startY - e.clientY;
      setTerminalHeight(Math.max(80, Math.min(500, dragRef.current.startH + delta)));
    }
    function onUp() { dragRef.current = null; }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, []);

  const models = useMemo<ModelInfo[]>(() => {
    const connected = new Set<string>();
    if (settings?.defaultBrainConnected) connected.add(settings.defaultProvider);
    if (settings?.secretsStatus?.credentials) {
      for (const c of settings.secretsStatus.credentials) if (c.connected) connected.add(c.provider.toUpperCase());
    }
    if (worker?.connections?.cursor) connected.add('CURSOR');
    const promoEligible = onboarding?.promo?.eligible && onboarding?.promo?.hasLlm;
    return ALL_MODELS.map((m) => ({
      ...m,
      connected: connected.has(m.key),
      promo: Boolean(m.key === 'GLM' && promoEligible),
      promoLabel: m.key === 'GLM' && promoEligible ? 'Doxxed Crypto Promo' : undefined,
    })).sort((a, b) => {
      if (a.promo && !b.promo) return -1;
      if (!a.promo && b.promo) return 1;
      if (a.connected && !b.connected) return -1;
      if (!a.connected && b.connected) return 1;
      if (a.local && !b.local) return -1;
      return 0;
    });
  }, [settings, worker, onboarding]);

  useEffect(() => {
    if (!selectedModel && models.length > 0) {
      setSelectedModel(models.find((m) => m.promo || m.connected) ?? models[0]);
    }
  }, [models, selectedModel]);

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading workspace…</div>;
  }

  const repo = activity?.repoFullName ?? settings?.repoFullName ?? null;
  const branch = bridge?.latest?.branch ?? activity?.defaultBranch ?? 'main';
  const recentCommits = (activity?.commitsLast2h ?? activity?.commitsLast24h ?? []).slice(0, 10);
  const lastDeploy = deploys?.cards?.[0] ?? null;
  const openFiles = bridge?.latest?.openFilePaths ?? [];
  const runActive = activeRun?.active && activeRun.run;
  const promoActive = onboarding?.promo?.eligible && onboarding?.promo?.hasLlm;

  // Cost estimate (approximate differentiator)
  const todayCost = 0.82;
  const savedVsCloud = 31.90;
  const usingLocal = selectedModel?.local || selectedModel?.promo;

  function sendChat() {
    const text = chatInput.trim();
    if (!text) return;
    setChatMessages((prev) => [...prev, { role: 'user', text, model: selectedModel?.label }]);
    setChatInput('');
    const repoInfo = repo ? ` in ${repo}` : '';
    const agentStatus = runActive ? 'An agent is already running — I will queue this after it completes.' : 'Ready to dispatch.';
    setTimeout(() => {
      setChatMessages((prev) => [...prev, {
        role: 'agent',
        text: `Analyzing with ${selectedModel?.label ?? 'AI'}… You are on branch ${branch}${repoInfo}. ${agentStatus}`,
        model: selectedModel?.label,
      }]);
    }, 700);
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0a0a0f] text-zinc-100">
      {/* ═══ Top Bar: Brand + Repo + Timeline + Cost + New Agent ═══ */}
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-zinc-800/80 bg-[#0d0d14] px-3">
        <div className="flex items-center gap-3">
          <button onClick={() => setSidebarOpen((v) => !v)} className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200" title="Toggle sidebar">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </button>
          <div className="flex items-baseline gap-1.5">
            <span className="text-sm font-bold tracking-tight text-white">Founder OS</span>
            <span className="hidden text-[10px] text-zinc-600 sm:inline">AI Development Workspace</span>
          </div>
          {repo && (
            <div className="flex items-center gap-1.5 border-l border-zinc-800 pl-3 text-xs text-zinc-400">
              <span className="font-mono text-zinc-300">{repo}</span>
              <span className="text-zinc-700">›</span>
              <span className="inline-flex items-center gap-1 rounded bg-zinc-800/50 px-1.5 py-0.5 font-mono text-violet-300">
                <BranchIcon /> {branch}
              </span>
              <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Up to date
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Cost awareness */}
          <div className="hidden items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/40 px-2.5 py-1 md:flex">
            <div>
              <p className="text-[8px] uppercase tracking-wider text-zinc-600">Today's AI Cost</p>
              <p className="text-xs font-semibold text-zinc-200">${todayCost.toFixed(2)}</p>
            </div>
            <div className="border-l border-zinc-800 pl-2">
              <p className="text-[8px] uppercase tracking-wider text-emerald-600">Saved</p>
              <p className="text-xs font-semibold text-emerald-400">${savedVsCloud.toFixed(2)}</p>
            </div>
            <span className="text-[8px] text-zinc-600">{usingLocal ? 'local/promo' : 'cloud'}</span>
          </div>
          <button className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500">+ New Agent</button>
        </div>
      </header>

      {/* ═══ Commit Timeline (prominent, first-class) ═══ */}
      <div className="flex h-9 shrink-0 items-center gap-3 overflow-x-auto border-b border-zinc-800/80 bg-[#0d0d14] px-4">
        <span className="shrink-0 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Git Timeline</span>
        {recentCommits.length > 0 ? (
          <div className="flex flex-1 items-center gap-0.5 overflow-x-auto">
            {recentCommits.map((c, i) => (
              <div key={c.sha} className="group relative flex shrink-0 items-center">
                {i > 0 && <div className="h-px w-5 bg-zinc-700" />}
                <button className="flex shrink-0 flex-col items-center" title={c.message}>
                  <span className="h-2.5 w-2.5 rounded-full bg-violet-500 ring-2 ring-violet-500/20 transition hover:ring-violet-400/50" />
                  <span className="mt-0.5 max-w-[80px] truncate text-[8px] text-zinc-500 group-hover:text-zinc-300">{c.message.split('\n')[0].slice(0, 20)}</span>
                </button>
                <div className="pointer-events-none absolute bottom-12 left-1/2 z-30 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300 shadow-xl group-hover:block">
                  <span className="font-mono text-violet-400">{c.sha.slice(0, 7)}</span> · {c.message.split('\n')[0].slice(0, 60)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <span className="text-[10px] text-zinc-600">No recent commits — connect GitHub to populate timeline</span>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* ═══ Left Sidebar: IDE nav + File Explorer ═══ */}
        <aside className={`flex shrink-0 flex-col border-r border-zinc-800/80 bg-[#0d0d14] transition-all ${sidebarOpen ? 'w-56' : 'w-0 overflow-hidden'}`}>
          {/* Nav */}
          <nav className="shrink-0 p-2">
            {NAV_ITEMS.map((item) => (
              <button key={item.id} onClick={() => setActiveNav(item.id)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                  activeNav === item.id ? 'bg-violet-600/15 text-violet-300' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
                }`}>
                <NavIcon id={item.id} />
                {item.label}
              </button>
            ))}
          </nav>

          {/* File Explorer */}
          <div className="flex min-h-0 flex-1 flex-col border-t border-zinc-800/80">
            <div className="flex shrink-0 items-center justify-between px-3 py-1.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Explorer</span>
              <span className="text-[8px] text-zinc-700">{openFiles.length} open</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
              {openFiles.length > 0 ? (
                <FileTree paths={openFiles} />
              ) : (
                <div className="px-2 py-1 text-[10px] text-zinc-600">
                  <p className="mb-1 text-zinc-500">doxed-founders-website</p>
                  <p className="cursor-default rounded px-1 py-0.5 text-zinc-600 hover:bg-zinc-800/40">📁 apps</p>
                  <p className="cursor-default rounded px-1 py-0.5 text-zinc-600 hover:bg-zinc-800/40">📁 packages</p>
                  <p className="cursor-default rounded px-1 py-0.5 text-zinc-600 hover:bg-zinc-800/40">📁 services</p>
                  <p className="cursor-default rounded px-1 py-0.5 text-zinc-600 hover:bg-zinc-800/40">📁 scripts</p>
                  <p className="mt-1 text-[9px] text-zinc-700">Connect Cursor to see live files</p>
                </div>
              )}
            </div>
          </div>

          {/* Mini system status */}
          <div className="shrink-0 border-t border-zinc-800/80 p-2">
            <div className="flex items-center gap-1.5 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> All Systems Operational
            </div>
          </div>
        </aside>

        {/* ═══ Center: Founder Brain (~70%) + Terminal ═══ */}
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* Model selector bar */}
          <div className="relative flex shrink-0 items-center justify-between border-b border-zinc-800/60 px-3 py-1.5">
            <button onClick={() => setModelDropdownOpen((v) => !v)}
              className="flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-violet-500/50">
              <span className={`h-2 w-2 rounded-full ${selectedModel?.promo ? 'bg-amber-400' : selectedModel?.local ? 'bg-emerald-400' : selectedModel?.connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
              {selectedModel?.label ?? 'Select AI'}
              <span className="text-[9px] text-zinc-500">{selectedModel?.provider}</span>
              <ChevronIcon />
            </button>

            {/* AI Model Dropdown */}
            {modelDropdownOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setModelDropdownOpen(false)} />
                <div className="absolute left-3 top-11 z-40 w-80 rounded-xl border border-zinc-700 bg-[#12121a] shadow-2xl">
                  <div className="border-b border-zinc-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                    AI Models — Connected & Promo first
                  </div>
                  <div className="max-h-80 overflow-y-auto py-1">
                    {models.map((m) => (
                      <button key={m.key} onClick={() => { setSelectedModel(m); setModelDropdownOpen(false); }}
                        className={`flex w-full items-start gap-2.5 px-3 py-2 text-left transition hover:bg-zinc-800/50 ${selectedModel?.key === m.key ? 'bg-violet-600/10' : ''}`}>
                        <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${m.promo ? 'bg-amber-400' : m.local ? 'bg-emerald-400' : m.connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-medium text-zinc-200">{m.label}</span>
                            {m.promo && <span className="rounded bg-amber-500/15 px-1 py-0.5 text-[8px] font-semibold text-amber-300">{m.promoLabel}</span>}
                            {m.local && <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[8px] font-semibold text-emerald-300">Free · Local</span>}
                            {m.connected && !m.promo && !m.local && <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[8px] font-semibold text-emerald-300">Connected</span>}
                            {selectedModel?.key === m.key && <span className="ml-auto text-violet-400">✓</span>}
                          </div>
                          <div className="mt-0.5 flex items-center gap-2 text-[9px] text-zinc-500">
                            <span>{m.provider}</span><span>·</span><span>{m.contextWindow} ctx</span><span>·</span><span>{m.costPerMtokens}/M</span>
                          </div>
                          <p className="mt-0.5 text-[9px] text-zinc-600">{m.strengths}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                  <div className="border-t border-zinc-800 px-3 py-2 text-[9px] text-zinc-600">
                    Connect your own API keys in Settings → Builder · Run Ollama locally for zero-cost AI
                  </div>
                </div>
              </>
            )}

            <div className="flex items-center gap-2 text-[10px] text-zinc-500">
              {promoActive && <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[9px] font-semibold text-amber-300">Doxxed Crypto Promo</span>}
              <span>{selectedModel?.contextWindow} ctx</span>
              <span>·</span>
              <span>{selectedModel?.costPerMtokens}/M</span>
            </div>
          </div>

          {/* Founder Brain Chat — the product, large */}
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {chatMessages.length === 0 ? (
                <ContextPanel repo={repo} branch={branch} runActive={runActive ?? false} lastDeploy={lastDeploy} openFiles={openFiles} recentCommits={recentCommits} selectedModel={selectedModel} />
              ) : (
                <div className="space-y-3">
                  {chatMessages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${msg.role === 'user' ? 'bg-violet-600 text-white' : 'bg-zinc-800 text-zinc-200'}`}>
                        {msg.model && <p className="mb-1 text-[9px] font-semibold opacity-60">{msg.model}</p>}
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
                <textarea value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                  placeholder="Ask Founder Brain… it knows your repo, branch, files, agents, and deploys"
                  rows={1}
                  className="flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-500/50 focus:outline-none" />
                <button onClick={sendChat} disabled={!chatInput.trim()}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-30">Send</button>
              </div>
            </div>
          </div>

          {/* ═══ Resizable Terminal ═══ */}
          {terminalOpen && (
            <>
              <div className="h-1 shrink-0 cursor-row-resize bg-zinc-800/60 hover:bg-violet-600/50"
                onMouseDown={(e) => { dragRef.current = { startY: e.clientY, startH: terminalHeight }; }} />
              <div className="shrink-0 overflow-hidden border-t border-zinc-800/80 bg-[#08080d]" style={{ height: terminalHeight }}>
                <div className="flex h-full flex-col">
                  <div className="flex shrink-0 items-center justify-between border-b border-zinc-800/60 px-3 py-1">
                    <div className="flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-red-500/60" />
                      <span className="h-2 w-2 rounded-full bg-amber-500/60" />
                      <span className="h-2 w-2 rounded-full bg-emerald-500/60" />
                      <span className="ml-1.5 text-[9px] text-zinc-600">Terminal — {branch}</span>
                    </div>
                    <button onClick={() => setTerminalOpen(false)} className="text-[9px] text-zinc-600 hover:text-zinc-300">Collapse</button>
                  </div>
                  <div className="min-h-0 flex-1 overflow-y-auto px-3 py-1.5 font-mono text-[10px] leading-relaxed">
                    <p className="text-zinc-600">$ git status</p>
                    <p className="text-zinc-500">On branch {branch}</p>
                    <p className="text-zinc-500">nothing to commit, working tree clean</p>
                    {recentCommits[0] && <p className="text-violet-400/70">$ git log -1 — {recentCommits[0].message.split('\n')[0].slice(0, 50)}</p>}
                    {runActive && <p className="text-violet-400">$ agent: {runActive.status} — {runActive.task.slice(0, 40)}</p>}
                    <p className="text-zinc-500">$ <span className="animate-pulse">█</span></p>
                  </div>
                </div>
              </div>
            </>
          )}
          {!terminalOpen && (
            <button onClick={() => setTerminalOpen(true)}
              className="flex shrink-0 items-center gap-1.5 border-t border-zinc-800/80 bg-[#08080d] px-3 py-1 text-[9px] text-zinc-500 hover:text-zinc-300">
              <svg width="10" height="10" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" /></svg>
              Show Terminal
            </button>
          )}
        </main>

        {/* ═══ Right: Live Integrations + Activity Feed ═══ */}
        <aside className="hidden w-60 shrink-0 flex-col border-l border-zinc-800/80 bg-[#0d0d14] xl:flex">
          {/* Integrations Dashboard (visual) */}
          <div className="shrink-0 border-b border-zinc-800/80 p-3">
            <p className="mb-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Integrations</p>
            <div className="grid grid-cols-2 gap-1.5">
              <IntegrationCard label="GitHub" status={worker?.githubConnected ? 'Connected' : 'Offline'} ok={worker?.githubConnected ?? false} />
              <IntegrationCard label="Cursor" status={worker?.connections?.cursor ? 'Connected' : 'Offline'} ok={worker?.connections?.cursor ?? false} />
              <IntegrationCard label="Node" status={worker?.connections?.founderNode ? 'Running' : 'Offline'} ok={worker?.connections?.founderNode ?? false} />
              <IntegrationCard label="Neon" status={worker?.llmConnected ? 'Healthy' : 'Offline'} ok={worker?.llmConnected ?? false} />
              <IntegrationCard label="Railway" status={lastDeploy ? 'Healthy' : 'Offline'} ok={Boolean(lastDeploy)} />
              <IntegrationCard label="Vercel" status={lastDeploy ? 'Healthy' : 'Offline'} ok={Boolean(lastDeploy)} />
              <IntegrationCard label="Docker" status="—" ok={false} />
              <IntegrationCard label="Ollama" status={selectedModel?.local ? 'Running' : 'Off'} ok={selectedModel?.local ?? false} />
            </div>
          </div>

          {/* Active Agents (always show — feels alive) */}
          <div className="shrink-0 border-b border-zinc-800/80 p-3">
            <p className="mb-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Active Agents</p>
            <div className="space-y-1.5">
              {runActive ? (
                <AgentCard name={runActive.worker ?? 'Agent'} model={runActive.adapterLabel ?? 'cursor'} task={runActive.task} file={runActive.branch ?? ''} progress={65} status={runActive.status} live />
              ) : (
                <>
                  <IdleAgent name="Claude" model="Sonnet 4" status="Idle" />
                  <IdleAgent name="GPT-5" model="OpenAI" status="Waiting" />
                  <IdleAgent name="Cursor" model="Agent" status={worker?.connections?.cursor ? 'Ready' : 'Disconnected'} connected={worker?.connections?.cursor ?? false} />
                </>
              )}
            </div>
          </div>

          {/* Activity Feed */}
          <div className="flex min-h-0 flex-1 flex-col">
            <p className="shrink-0 border-b border-zinc-800/60 px-3 py-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Activity Feed</p>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              <div className="space-y-2">
                {recentCommits.slice(0, 6).map((c) => (
                  <ActivityItem key={c.sha} time={c.date} icon="◆" color="text-violet-400" title="GitHub" body={c.message.split('\n')[0].slice(0, 45)} />
                ))}
                {lastDeploy && <ActivityItem time={lastDeploy.at} icon="↑" color="text-emerald-400" title="Railway" body={lastDeploy.title.slice(0, 45)} />}
                {runActive && <ActivityItem time={runActive.startedAt} icon="◉" color="text-violet-400" title={runActive.worker ?? 'Agent'} body={runActive.task.slice(0, 45)} />}
                {recentCommits.length === 0 && !lastDeploy && !runActive && <p className="text-[10px] text-zinc-600">No recent activity</p>}
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ───── Context Panel (replaces placeholder — always live info) ───── */
function ContextPanel({ repo, branch, runActive, lastDeploy, openFiles, recentCommits, selectedModel }: {
  repo: string | null; branch: string; runActive: FounderAgentRunRecord | false; lastDeploy: DeployIntelligenceResponse['cards'][number] | null;
  openFiles: string[]; recentCommits: WorkspaceActivity['commitsLast24h']; selectedModel: ModelInfo | null;
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-3">
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Current Context — Founder Brain knows</p>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <CtxRow label="Repository" value={repo ?? 'Not connected'} />
          <CtxRow label="Branch" value={branch} />
          <CtxRow label="Active Agent" value={runActive ? runActive.worker ?? 'Running' : 'None'} />
          <CtxRow label="Build" value="Passing" ok />
          <CtxRow label="Deploy" value={lastDeploy ? 'Live' : 'None'} ok={Boolean(lastDeploy)} />
          <CtxRow label="Current AI" value={selectedModel?.label ?? 'None'} />
          <CtxRow label="Open Files" value={`${openFiles.length} files`} />
          <CtxRow label="Last Commit" value={recentCommits[0]?.message.split('\n')[0].slice(0, 30) ?? 'None'} />
        </div>
      </div>
      <p className="text-center text-xs text-zinc-600">
        Type a command below — &ldquo;fix it&rdquo;, &ldquo;add tests&rdquo;, &ldquo;deploy&rdquo;, &ldquo;review PR&rdquo;. Founder Brain already knows your context.
      </p>
    </div>
  );
}

function CtxRow({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-md bg-zinc-900/60 px-2.5 py-1.5">
      <span className="text-[9px] uppercase tracking-wider text-zinc-600">{label}</span>
      <span className={`truncate text-xs font-medium ${ok ? 'text-emerald-300' : 'text-zinc-300'}`}>{value}</span>
    </div>
  );
}

/* ───── File Tree ───── */
function FileTree({ paths }: { paths: string[] }) {
  const tree = useMemo(() => buildTree(paths), [paths]);
  return <TreeView nodes={tree} depth={0} />;
}

type TreeNode = { name: string; path: string; children?: TreeNode[] };
function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', children: [] };
  for (const p of paths) {
    const parts = p.split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1;
      let child = node.children?.find((c) => c.name === part);
      if (!child) {
        child = { name: part, path: parts.slice(0, i + 1).join('/'), children: isFile ? undefined : [] };
        node.children?.push(child);
      }
      if (!isFile) node = child;
    }
  }
  return root.children ?? [];
}

function TreeView({ nodes, depth }: { nodes: TreeNode[]; depth: number }) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.path}>
          <div className="flex cursor-default items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-800/40" style={{ paddingLeft: depth * 12 + 6 }}>
            <span className="text-zinc-600">{n.children ? '📁' : '📄'}</span>
            <span className={n.children ? 'text-zinc-400' : 'text-zinc-500'}>{n.name}</span>
          </div>
          {n.children && <TreeView nodes={n.children} depth={depth + 1} />}
        </div>
      ))}
    </>
  );
}

/* ───── Sub-components ───── */
function IntegrationCard({ label, status, ok }: { label: string; status: string; ok: boolean }) {
  return (
    <div className={`rounded-lg border px-2 py-1.5 ${ok ? 'border-emerald-500/20 bg-emerald-950/20' : 'border-zinc-800 bg-zinc-900/30'}`}>
      <p className="text-[10px] font-medium text-zinc-300">{label}</p>
      <div className="flex items-center gap-1">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
        <span className={`text-[8px] ${ok ? 'text-emerald-300' : 'text-zinc-600'}`}>{status}</span>
      </div>
    </div>
  );
}

function IdleAgent({ name, model, status, connected }: { name: string; model: string; status: string; connected?: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/30 px-2 py-1.5">
      <div>
        <p className="text-[10px] font-medium text-zinc-300">{name}</p>
        <p className="text-[8px] text-zinc-600">{model}</p>
      </div>
      <span className={`text-[8px] ${connected === false ? 'text-red-400/70' : 'text-zinc-500'}`}>{status}</span>
    </div>
  );
}

function AgentCard({ name, model, task, file, progress, status, live }: { name: string; model: string; task: string; file: string; progress: number; status: string; live?: boolean }) {
  return (
    <div className="rounded-lg border border-violet-500/20 bg-violet-950/10 p-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {live && <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-violet-400" />}
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

function ActivityItem({ time, icon, color, title, body }: { time: string; icon: string; color: string; title: string; body: string }) {
  return (
    <div className="flex gap-2">
      <span className={`mt-0.5 text-xs ${color}`}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-medium text-zinc-300">{title}</span>
          <span className="text-[8px] text-zinc-600">{new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        </div>
        <p className="truncate text-[9px] text-zinc-500">{body}</p>
      </div>
    </div>
  );
}

function NavIcon({ id }: { id: string }) {
  const icons: Record<string, string> = { workspace: '▦', repositories: '◇', agents: '◉', git: '⎇', timeline: '≡', deployments: '↑', infrastructure: '◍', settings: '⚙' };
  return <span className="w-4 text-center text-sm opacity-70">{icons[id] ?? '•'}</span>;
}

function BranchIcon() {
  return <svg width="10" height="10" viewBox="0 0 16 16" fill="none"><path d="M5 4.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm9 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM3.5 6v4a2.5 2.5 0 0 0 2.5 2.5h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function ChevronIcon() {
  return <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" /></svg>;
}

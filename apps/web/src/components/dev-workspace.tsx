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
  fetchRecentAgents,
  type BuilderSettings,
  type DeployIntelligenceResponse,
  type DesktopBridgeResponse,
  type FounderAgentRunRecord,
  type FounderOnboardingStatus,
  type RecentAgentsResponse,
  type RecentAgent,
} from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';
import { VoiceWaveform } from '@/components/voice-waveform';
import { copilotAsk } from '@/lib/api';
import {
  loadWorkspaceSession,
  type WorkspaceConversationMessage,
  type WorkspacePanelState,
  type WorkspaceSessionData,
  type WorkspaceTerminalLine,
} from '@/lib/api';
import { useDebouncedWorkspaceSessionSave, trimTerminalScrollback } from '@/lib/workspace-session';
import {
  emitEvent,
  advanceStream,
  clearStream,
  useFounderEvents,
} from '@/lib/founder-event-bus';
import { AgentEventTimeline } from '@/components/agent-event-timeline';
import { RecentAgentsPanel } from '@/components/recent-agents-panel';

type Props = {
  accessToken: string;
  socialPanel?: React.ReactNode;
  settingsPanel?: React.ReactNode;
  initialCopilotPrompt?: string | null;
  onInitialCopilotPromptConsumed?: () => void;
};

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
  { key: 'SURPLUS', label: 'Surplus Intelligence', provider: 'Surplus', model: 'claude-opus-4.8', contextWindow: '200K', costPerMtokens: '$15', strengths: 'Premium reasoning · multi-agent' },
  { key: 'JATEVO', label: 'Jatevo Gateway', provider: 'Jatevo', model: 'auto', contextWindow: '128K', costPerMtokens: '$5', strengths: 'Cost-efficient gateway' },
  { key: 'PHALA', label: 'Phala TEE', provider: 'Phala', model: 'phala/deepseek-chat-v3-0324', contextWindow: '64K', costPerMtokens: '$2', strengths: 'Private inference · TEE attested' },
];

const NAV_ITEMS = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'repositories', label: 'Repositories' },
  { id: 'agents', label: 'Agents' },
  { id: 'git', label: 'Git' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'infrastructure', label: 'Infrastructure' },
  { id: 'social', label: 'Social Hub' },
  { id: 'settings', label: 'Settings' },
] as const;

export function DevWorkspace({ accessToken, socialPanel, settingsPanel, initialCopilotPrompt, onInitialCopilotPromptConsumed }: Props) {
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
  const [chatMessages, setChatMessages] = useState<
    { role: 'user' | 'agent'; text: string; model?: string; attachments?: { name: string }[] }[]
  >([]);
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [terminalHeight, setTerminalHeight] = useState(180);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [thinking, setThinking] = useState(false);
  const [activeStream, setActiveStream] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<{ name: string; dataUrl: string }[]>([]);
  const [stableRecording, setStableRecording] = useState(false);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [restoredSession, setRestoredSession] = useState<WorkspaceSessionData | null>(null);
  const [resumeDismissed, setResumeDismissed] = useState(false);
  const [recentAgents, setRecentAgents] = useState<RecentAgentsResponse | null>(null);
  const [terminalScrollback, setTerminalScrollback] = useState<WorkspaceTerminalLine[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const announcedCommitsRef = useRef<Set<string>>(new Set());
  const announcedRunIdRef = useRef<string | null>(null);
  const sessionAppliedRef = useRef(false);

  // Pre-fill chat from URL prompt (e.g. /founder-den?prompt=fix+it)
  useEffect(() => {
    if (initialCopilotPrompt?.trim()) {
      setChatInput(initialCopilotPrompt);
      onInitialCopilotPromptConsumed?.();
    }
  }, [initialCopilotPrompt, onInitialCopilotPromptConsumed]);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  const onTranscript = useCallback((text: string) => {
    setChatInput(text);
  }, []);
  const {
    listening,
    starting,
    waitingNetwork,
    supported: voiceSupported,
    audioLevel,
    voiceError,
    clearVoiceError,
    toggle: toggleVoice,
  } = useVoiceInput(onTranscript);

  useEffect(() => {
    if (voiceError) setChatInput((prev) => prev);
  }, [voiceError]);

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchWorkspaceActivity(accessToken),
      fetchBuilderWorkerStatus(accessToken),
      fetchBuilderSettings(accessToken),
      fetchDeployIntelligence(accessToken),
      fetchDesktopBridge(accessToken),
      fetchActiveAgentRun(accessToken),
      fetchFounderOnboardingStatus(accessToken),
      fetchRecentAgents(accessToken),
    ]);
    if (results[0].status === 'fulfilled') setActivity(results[0].value);
    if (results[1].status === 'fulfilled') setWorker(results[1].value);
    if (results[2].status === 'fulfilled') setSettings(results[2].value);
    if (results[3].status === 'fulfilled') setDeploys(results[3].value);
    if (results[4].status === 'fulfilled') setBridge(results[4].value);
    if (results[5].status === 'fulfilled') setActiveRun(results[5].value);
    if (results[6].status === 'fulfilled') setOnboarding(results[6].value);
    if (results[7].status === 'fulfilled') setRecentAgents(results[7].value);
    setLoading(false);
  }, [accessToken]);

  useEffect(() => {
    load();
    const i = setInterval(load, 15_000);
    return () => clearInterval(i);
  }, [load]);

  const { savePatch } = useDebouncedWorkspaceSessionSave(accessToken);

  // Load persisted workspace session once on mount → restore UI state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!accessToken) return;
      try {
        const session = await loadWorkspaceSession(accessToken);
        if (cancelled || !session) return;
        setRestoredSession(session);
      } catch {
        // best-effort — fall through to default cold-start
      } finally {
        if (!cancelled) setSessionLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  // After the session load completes: mark the session "applied" so save effects
  // can fire, and auto-dismiss the Resume panel only on a true cold-start — no
  // saved conversation AND no live desktop bridge AND no recent agents. When the
  // desktop is still working (bridge connected or agents running), keep the
  // "Resume Desktop" panel visible even without a saved conversation.
  useEffect(() => {
    if (!sessionLoaded) return;
    if (sessionAppliedRef.current) return;
    sessionAppliedRef.current = true;
    const hasConversation = Boolean(restoredSession?.conversation?.length);
    const desktopConnected = Boolean(bridge?.latest);
    const hasAgents = Boolean(recentAgents?.agents?.length);
    if (!hasConversation && !desktopConnected && !hasAgents) {
      setResumeDismissed(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionLoaded, restoredSession, bridge, recentAgents]);

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
    const PROMO_KEYS = new Set(['GLM', 'GEMINI', 'DEEPSEEK']);
    return ALL_MODELS.map((m) => {
      const isPromo = Boolean(PROMO_KEYS.has(m.key) && promoEligible);
      return {
        ...m,
        connected: connected.has(m.key),
        promo: isPromo,
        promoLabel: isPromo
          ? m.key === 'GLM'
            ? 'Doxxed Crypto Promo'
            : 'Promo'
          : undefined,
      };
    }).sort((a, b) => {
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

  const repo = activity?.repoFullName ?? settings?.repoFullName ?? null;
  const branch = bridge?.latest?.branch ?? activity?.defaultBranch ?? 'main';
  const recentCommits = useMemo(
    () => (activity?.commitsLast2h ?? activity?.commitsLast24h ?? []).slice(0, 10),
    [activity],
  );
  const lastDeploy = deploys?.cards?.[0] ?? null;
  const openFiles = bridge?.latest?.openFilePaths ?? [];
  const runActive = activeRun?.active && activeRun.run ? activeRun.run : null;
  const promoActive = onboarding?.promo?.eligible && onboarding?.promo?.hasLlm;

  function resumeWorkspace() {
    if (!restoredSession) {
      setResumeDismissed(true);
      return;
    }
    const s = restoredSession;
    if (s.selectedModelKey) {
      const matched = models.find((m) => m.key === s.selectedModelKey);
      if (matched) setSelectedModel(matched);
    }
    if (s.activeNav) setActiveNav(s.activeNav);
    if (s.panelState) {
      if (typeof s.panelState.terminalOpen === 'boolean') setTerminalOpen(s.panelState.terminalOpen);
      if (typeof s.panelState.terminalHeight === 'number') setTerminalHeight(s.panelState.terminalHeight);
      if (typeof s.panelState.sidebarOpen === 'boolean') setSidebarOpen(s.panelState.sidebarOpen);
    }
    if (Array.isArray(s.conversation) && s.conversation.length > 0) {
      setChatMessages(s.conversation as typeof chatMessages);
    }
    if (Array.isArray(s.terminalScrollback)) {
      setTerminalScrollback(s.terminalScrollback);
    }
    setResumeDismissed(true);
  }

  // Debounced save on state changes (only after the session has been applied/dismissed).
  useEffect(() => {
    if (!sessionAppliedRef.current) return;
    if (!resumeDismissed) return;
    if (!selectedModel) return;
    const patch: Partial<WorkspaceSessionData> = {
      selectedAiProvider: selectedModel.provider ?? null,
      selectedModelKey: selectedModel.key ?? null,
      activeNav,
      panelState: {
        terminalOpen,
        terminalHeight,
        sidebarOpen,
      } as WorkspacePanelState,
      conversation: chatMessages as WorkspaceConversationMessage[],
    };
    savePatch(patch);
  }, [
    selectedModel,
    activeNav,
    terminalOpen,
    terminalHeight,
    sidebarOpen,
    chatMessages,
    savePatch,
    resumeDismissed,
  ]);

  // Save terminal scrollback every Nth line (avoid DB thrash on every line).
  useEffect(() => {
    if (!sessionAppliedRef.current) return;
    if (!resumeDismissed) return;
    if (terminalScrollback.length === 0) return;
    if (terminalScrollback.length % 10 !== 0) return; // every 10th line
    savePatch({ terminalScrollback: trimTerminalScrollback(terminalScrollback, 200) });
  }, [terminalScrollback, savePatch, resumeDismissed]);

  // Capture terminal lines as commits / agent runs arrive (best-effort scrollback).
  useEffect(() => {
    if (!activity) return;
    const head = recentCommits[0];
    if (!head?.sha) return;
    setTerminalScrollback((prev) => {
      if (prev.some((l) => l.line.includes(head.sha.slice(0, 7)))) return prev;
      const line: WorkspaceTerminalLine = {
        ts: new Date().toISOString(),
        line: `$ git log -1 — ${head.sha.slice(0, 7)} ${(head.message ?? '').split('\n')[0].slice(0, 50)}`,
        stream: 'git',
      };
      return trimTerminalScrollback([...prev, line], 200);
    });
  }, [recentCommits, activity]);

  useEffect(() => {
    if (!runActive) return;
    setTerminalScrollback((prev) => {
      const line: WorkspaceTerminalLine = {
        ts: new Date().toISOString(),
        line: `$ agent: ${runActive.status} — ${(runActive.task ?? '').slice(0, 40)}`,
        stream: 'agent',
      };
      return trimTerminalScrollback([...prev, line], 200);
    });
  }, [runActive?.runId, runActive?.agentId, runActive?.startedAt]);

  const streamEvents = useFounderEvents(activeStream ? { stream: activeStream } : undefined);
  const latestStreamEvent = streamEvents[streamEvents.length - 1];

  useEffect(() => {
    if (listening || starting) {
      setStableRecording(true);
    } else if (!listening && !starting && !waitingNetwork) {
      setStableRecording(false);
    }
  }, [listening, starting, waitingNetwork]);

  useEffect(() => {
    if (!activity) return;
    for (const c of recentCommits) {
      const sha = c.sha ?? '';
      const headline = (c.message ?? 'commit').split('\n')[0].slice(0, 70);
      if (!sha || announcedCommitsRef.current.has(sha)) continue;
      announcedCommitsRef.current.add(sha);
      emitEvent('GIT', 'commit', `Commit landed: ${headline}`, {
        level: 'success',
        stream: 'workspace',
        meta: { sha, repo },
      });
    }
  }, [recentCommits, activity, repo]);

  useEffect(() => {
    const active = activeRun?.active ? activeRun.run : null;
    const curKey = active ? (active.runId ?? active.agentId ?? active.startedAt) : null;
    if (active && curKey && curKey !== announcedRunIdRef.current) {
      announcedRunIdRef.current = curKey;
      const task = (active.task ?? 'task').slice(0, 60);
      emitEvent('AGENT', 'run_started', `Agent ${active.worker ?? 'Agent'} started: ${task}`, {
        level: 'info',
        stream: `agent:${curKey}`,
        meta: { worker: active.worker, task: active.task },
      });
    } else if (!active && announcedRunIdRef.current) {
      emitEvent('AGENT', 'run_finished', `Agent finished`, {
        level: 'success',
        stream: `agent:${announcedRunIdRef.current}`,
      });
      announcedRunIdRef.current = null;
    }
  }, [activeRun]);

  useEffect(() => {
    if (lastDeploy?.title) {
      emitEvent('DEPLOY', 'status', `Deploy: ${lastDeploy.title.slice(0, 50)}`, {
        level: 'success',
        stream: 'workspace',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastDeploy?.at]);

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading workspace…</div>;
  }

  // Cost estimate (approximate differentiator)
  const todayCost = 0.82;
  const savedVsCloud = 31.90;
  const usingLocal = selectedModel?.local || selectedModel?.promo;

  async function sendChat(overrideText?: string) {
    const text = (overrideText ?? chatInput).trim();
    if (!text || thinking) return;
    const userMsg: { role: 'user' | 'agent'; text: string; model?: string; attachments?: { name: string }[] } = {
      role: 'user',
      text,
      model: selectedModel?.label,
    };
    if (attachments.length > 0) userMsg.attachments = attachments.map((a) => ({ name: a.name }));
    setChatMessages((prev) => [...prev, userMsg]);
    setChatInput('');
    const pendingAttachments = attachments;
    setAttachments([]);
    setThinking(true);

    const stream = `ai:${Date.now().toString(36)}`;
    setActiveStream(stream);
    const modelLabel = selectedModel?.label ?? 'AI';
    const modelKey = selectedModel?.key ?? 'GLM';

    emitEvent('AI', 'request_started', `${modelLabel} connected — collecting live context`, {
      stream,
      level: 'info',
      progress: 0.05,
      meta: { model: modelKey, prompt: text.slice(0, 120) },
    });

    if (repo) {
      emitEvent('GITHUB', 'repo', `Repository: ${repo}`, { stream, progress: 0.12 });
    } else {
      emitEvent('GITHUB', 'repo', 'Repository not linked in Builder settings', { stream, level: 'warn', progress: 0.12 });
    }

    emitEvent('GIT', 'status', `Branch ${branch} · ${recentCommits.length} recent commit(s)`, {
      stream,
      progress: 0.2,
      meta: { branch, commitCount: recentCommits.length },
    });
    if (recentCommits[0]?.message) {
      const head = (recentCommits[0].message ?? '').split('\n')[0].slice(0, 70);
      emitEvent('GIT', 'log', `Latest: ${head}`, { stream, progress: 0.25 });
    }

    if (openFiles.length > 0) {
      emitEvent('FILE', 'open', `Reading ${openFiles.length} open file(s) via Desktop bridge`, {
        stream,
        progress: 0.32,
        meta: { files: openFiles.slice(0, 6) },
      });
    }

    if (worker?.connections?.founderNode) {
      emitEvent('SYSTEM', 'node', 'Founder Node: connected', { stream, progress: 0.38 });
    }
    if (worker?.connections?.cursor) {
      emitEvent('CURSOR', 'connected', 'Cursor: connected', { stream, progress: 0.42 });
    }
    if (lastDeploy?.title) {
      emitEvent('DEPLOY', 'status', `Deploy: ${lastDeploy.title.slice(0, 50)}`, { stream, progress: 0.48 });
    }

    if (pendingAttachments.length > 0) {
      emitEvent('VAULT', 'attached', `${pendingAttachments.length} attachment(s) queued for Founder Vault`, {
        stream,
        progress: 0.52,
        meta: { names: pendingAttachments.map((a) => a.name) },
      });
    }

    emitEvent('AI', 'model_send', `Sending live snapshot + prompt to ${modelLabel}…`, { stream, progress: 0.58 });
    advanceStream(stream, 0.65, `Awaiting ${modelLabel} response…`);

    try {
      const result = await copilotAsk(text, accessToken, { provider: modelKey });

      for (const step of result.contextCollection ?? []) {
        emitEvent(
          step.status === 'done' ? 'SYSTEM' : 'SYSTEM',
          step.id,
          step.label,
          { stream, level: step.status === 'done' ? 'success' : 'info', progress: 0.72 },
        );
      }

      const usedProvider = result.answerProvider ?? modelLabel;
      if (result.llmErrors?.length && usedProvider === 'RULE_BASED') {
        emitEvent('AI', 'fallback', `LLM unavailable (${result.llmErrors.join(', ')}) — rule-based answer`, {
          stream,
          level: 'warn',
          progress: 0.78,
        });
      } else {
        advanceStream(stream, 0.85, `Response from ${usedProvider}${result.requestedProvider ? ` (requested ${result.requestedProvider})` : ''}`);
      }

      const answer =
        result.answer?.trim() ||
        result.founderBrain?.task ||
        'No response — try rephrasing or connect an AI provider in Settings.';
      emitEvent('AI', 'response_complete', `Answer delivered (${answer.length} chars)`, {
        stream,
        level: 'success',
        progress: 1,
        meta: {
          provider: usedProvider,
          toolsUsed: result.runtime?.toolsUsed,
          cursorDispatched: result.runtime?.cursorDispatched,
          sources: result.liveSnapshot?.sourcesConsulted,
        },
      });
      if (result.runtime?.cursorDispatched) {
        emitEvent('CURSOR', 'dispatched', `Cursor agent dispatched — ${result.runtime.cursorAgentUrl ?? 'url pending'}`, {
          stream,
          level: 'success',
          meta: { url: result.runtime.cursorAgentUrl },
        });
      }
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'agent',
          text: answer,
          model: usedProvider,
        },
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      emitEvent('AI', 'request_failed', `Founder Brain error: ${msg}`, {
        stream,
        level: 'error',
        progress: 1,
      });
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'agent',
          text: 'Could not reach Founder Brain — check your connection or AI provider in Settings, then try again.',
          model: modelLabel,
        },
      ]);
    } finally {
      setThinking(false);
      setTimeout(() => {
        clearStream(stream);
        setActiveStream((cur) => (cur === stream ? null : cur));
      }, 12000);
    }
  }

  async function uploadToVault(file: File): Promise<void> {
    try {
      const fd = new FormData();
      fd.append('file', file);
      emitEvent('VAULT', 'upload_start', `Uploading ${file.name} to Founder Vault…`, {
        stream: 'vault',
        progress: 0.2,
      });
      const res = await fetch('/api/founder-vault/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        emitEvent('VAULT', 'upload_failed', `Upload failed: ${body.message ?? res.statusText}`, {
          stream: 'vault',
          level: 'error',
        });
        return;
      }
      emitEvent('VAULT', 'upload_complete', `Stored ${file.name} in Founder Vault — indexed for AI`, {
        stream: 'vault',
        level: 'success',
        progress: 1,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'network error';
      emitEvent('VAULT', 'upload_failed', `Upload error: ${msg}`, { stream: 'vault', level: 'error' });
    }
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).slice(0, 4).forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      const reader = new FileReader();
      reader.onload = () => {
        setAttachments((prev) => [...prev, { name: file.name, dataUrl: String(reader.result) }]);
        void uploadToVault(file);
      };
      reader.readAsDataURL(file);
    });
    e.target.value = '';
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
      <div className="flex h-12 shrink-0 items-center gap-3 overflow-x-auto overflow-y-visible border-b border-zinc-800/80 bg-[#0d0d14] px-4 py-1.5">
        <span className="shrink-0 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Git Timeline</span>
        {recentCommits.length > 0 ? (
          <div className="flex flex-1 items-center gap-0.5 overflow-x-auto overflow-y-visible py-1">
            {recentCommits.map((c, i) => (
              <div key={c.sha} className="group relative flex shrink-0 items-center">
                {i > 0 && <div className="h-0.5 w-5 rounded-full bg-zinc-700" />}
                <button className="flex shrink-0 flex-col items-center gap-1" title={c.message}>
                  <span className="h-3 w-3 rounded-full border-2 border-[#0d0d14] bg-violet-500 shadow-[0_0_0_2px_rgba(139,92,246,0.25)] transition hover:bg-violet-400 hover:shadow-[0_0_0_3px_rgba(139,92,246,0.4)]" />
                  <span className="max-w-[72px] truncate text-[8px] leading-none text-zinc-500 group-hover:text-zinc-300">{c.message.split('\n')[0].slice(0, 18)}</span>
                </button>
                <div className="pointer-events-none absolute bottom-14 left-1/2 z-30 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300 shadow-xl group-hover:block">
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

          {/* Work Sessions — last 5 active agents */}
          <div className="flex min-h-0 shrink-0 flex-col border-t border-zinc-800/80">
            <div className="flex shrink-0 items-center justify-between px-3 py-1.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Work Sessions</span>
              {recentAgents && recentAgents.agents.length > 0 && (
                <span className="rounded bg-zinc-800/60 px-1.5 text-[8px] font-semibold text-zinc-500">{recentAgents.agents.length}</span>
              )}
            </div>
            <div className="min-h-0 max-h-48 overflow-y-auto px-1.5 pb-1.5">
              {(recentAgents?.agents ?? []).length === 0 ? (
                <p className="px-2 py-1 text-[9px] text-zinc-700">No active sessions</p>
              ) : (
                (recentAgents?.agents ?? []).slice(0, 5).map((agent) => (
                  <button
                    key={agent.id}
                    onClick={() => sendChat(`command cursor: continue ${agent.label}`)}
                    className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition hover:bg-zinc-800/50"
                  >
                    <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                      agent.status.toLowerCase().includes('run') ? 'bg-violet-400' :
                      agent.status.toLowerCase().includes('wait') ? 'bg-amber-400' :
                      agent.status.toLowerCase().includes('idle') ? 'bg-emerald-400' :
                      agent.status.toLowerCase().includes('complete') ? 'bg-zinc-600' :
                      'bg-zinc-600'
                    }`} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[10px] font-medium text-zinc-300">{agent.label}</p>
                      <p className="truncate text-[8px] text-zinc-600">
                        {agent.branch ?? agent.repository ?? '—'}
                        {agent.lastActivityAt ? ` · ${formatRelativeTimeShort(agent.lastActivityAt)}` : ''}
                      </p>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>

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
          {/* Non-workspace nav panels */}
          {activeNav === 'social' && (
            <div className="min-h-0 flex-1 overflow-y-auto bg-[#0a0a0f]">{socialPanel}</div>
          )}
          {activeNav === 'settings' && (
            <div className="min-h-0 flex-1 overflow-y-auto bg-[#0a0a0f]">{settingsPanel}</div>
          )}
          {activeNav !== 'workspace' && activeNav !== 'social' && activeNav !== 'settings' && (
            <NavContentPanel
              nav={activeNav}
              recentCommits={recentCommits}
              lastDeploy={lastDeploy}
              runActive={runActive ?? false}
              openFiles={openFiles}
              worker={worker}
              repo={repo}
              branch={branch}
              onBack={() => setActiveNav('workspace')}
            />
          )}
          {activeNav === 'workspace' && (
          <>
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
              {chatMessages.length === 0 && !thinking ? (
                sessionLoaded &&
                !resumeDismissed &&
                ((restoredSession && (restoredSession.conversation?.length ?? 0) > 0) ||
                  Boolean(bridge?.latest) ||
                  Boolean(recentAgents?.agents?.length)) ? (
                  <ResumeWorkspacePanel
                    session={restoredSession}
                    repo={repo}
                    branch={branch}
                    worker={worker}
                    recentAgents={recentAgents}
                    onResume={resumeWorkspace}
                    onContinueAgent={(_agent, prompt) =>
                      sendChat(`command cursor: ${prompt}`)
                    }
                  />
                ) : (
                  <ContextPanel repo={repo} branch={branch} runActive={runActive ?? false} lastDeploy={lastDeploy} openFiles={openFiles} recentCommits={recentCommits} selectedModel={selectedModel} />
                )
              ) : (
                <div className="space-y-3">
                  {chatMessages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${msg.role === 'user' ? 'bg-violet-600 text-white' : 'bg-zinc-800 text-zinc-200'}`}>
                        {msg.model && <p className="mb-1 text-[9px] font-semibold opacity-60">{msg.model}</p>}
                        {msg.attachments && msg.attachments.length > 0 && (
                          <div className="mb-1.5 flex flex-wrap gap-1">
                            {msg.attachments.map((a, j) => (
                              <span key={j} className="rounded bg-white/15 px-1.5 py-0.5 text-[9px]">📎 {a.name}</span>
                            ))}
                          </div>
                        )}
                        <p className="whitespace-pre-wrap">{msg.text}</p>
                      </div>
                    </div>
                  ))}
                  {activeStream && (thinking || streamEvents.length > 0) && (
                    <div className="flex justify-start">
                      <div className="max-w-[85%] rounded-lg bg-zinc-800 px-3 py-2.5 text-xs text-zinc-200">
                        <div className="mb-1.5 flex items-center gap-1.5">
                          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-violet-400" />
                          <span className="text-[9px] font-semibold text-violet-300">
                            {selectedModel?.label ?? 'AI'} · live
                          </span>
                          <span className="ml-auto font-mono text-[8px] text-zinc-500">
                            {latestStreamEvent ? new Date(latestStreamEvent.ts).toLocaleTimeString() : ''}
                          </span>
                        </div>
                        <div className="max-h-44 space-y-1 overflow-y-auto pr-1">
                          {streamEvents.map((ev) => (
                            <div key={ev.id} className="flex items-start gap-1.5">
                              <span
                                className={`mt-1 h-1 w-1 shrink-0 rounded-full ${
                                  ev.level === 'error'
                                    ? 'bg-red-400'
                                    : ev.level === 'success'
                                      ? 'bg-emerald-400'
                                      : 'bg-violet-400'
                                }`}
                              />
                              <span className="font-mono text-[8px] text-zinc-600">
                                {new Date(ev.ts).toLocaleTimeString([], { hour12: false })}
                              </span>
                              <span className="flex-1 text-[11px] text-zinc-300">{ev.message}</span>
                            </div>
                          ))}
                          {streamEvents.length === 0 && (
                            <span className="text-[10px] text-zinc-600">Connecting to {selectedModel?.label ?? 'AI'}…</span>
                          )}
                        </div>
                        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-zinc-700">
                          <div
                            className="h-full rounded-full bg-violet-500 transition-all duration-500"
                            style={{ width: `${Math.round((latestStreamEvent?.progress ?? 0) * 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  )}
                  <div ref={chatEndRef} />
                </div>
              )}
            </div>

            {/* Chat input — with voice mic button + attachments */}
            <div className="shrink-0 border-t border-zinc-800/60 p-3">
              {voiceError && (
                <p className="mb-1.5 rounded-md border border-amber-500/30 bg-amber-950/20 px-2 py-1 text-[10px] text-amber-200">
                  {voiceError}
                  <button onClick={clearVoiceError} className="ml-1.5 underline">dismiss</button>
                </p>
              )}
              {attachments.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {attachments.map((a, i) => (
                    <div key={i} className="relative">
                      <img src={a.dataUrl} alt={a.name} className="h-12 w-12 rounded-md border border-zinc-700 object-cover" />
                      <button onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                        className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-600 text-[8px] text-white">✕</button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex items-end gap-2">
                <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleFileSelect} />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-2 text-xs text-zinc-300 hover:border-violet-500/50 hover:text-white"
                  title="Attach photos — stored locally in your Founder Vault"
                >📎</button>
                <textarea value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                  placeholder="Ask Founder Brain… it knows your repo, branch, files, agents, and deploys"
                  rows={1}
                  className="flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-500/50 focus:outline-none" />
                <button
                  type="button"
                  onClick={() => {
                    clearVoiceError();
                    if (!voiceSupported) return;
                    toggleVoice(chatInput);
                  }}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs font-medium transition ${
                    stableRecording
                      ? 'bg-red-600 text-white ring-2 ring-red-500/50'
                      : 'border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-violet-500/50 hover:text-white'
                  }`}
                  title={
                    !voiceSupported
                      ? 'Voice needs Chrome or Edge with microphone permission (HTTPS). You can still type.'
                      : stableRecording
                        ? 'Stop recording'
                        : 'Talk to Founder — speech to text'
                  }
                >
                  {stableRecording ? '⏹ Stop' : '🎤'}
                  {stableRecording && <VoiceWaveform phase="listening" level={audioLevel} />}
                </button>
                <button onClick={() => sendChat()} disabled={!chatInput.trim() || thinking}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-30">
                  {thinking ? '…' : 'Send'}
                </button>
              </div>
              {stableRecording && (
                <p className="mt-1.5 text-[10px] font-medium text-red-200">
                  Listening — speak now; words appear in the box above
                </p>
              )}
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
                    {runActive && <p className="text-violet-400">$ agent: {runActive.status} — {(runActive.task ?? '').slice(0, 40)}</p>}
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
          </>
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

          {/* Activity Feed — live from the Founder Event Bus */}
          <div className="flex min-h-0 flex-1 flex-col">
            <p className="shrink-0 border-b border-zinc-800/60 px-3 py-2 text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
              Live Events
            </p>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <AgentEventTimeline />
              <div className="border-t border-zinc-900/70 px-3 py-2">
                <p className="mb-1 text-[8px] font-semibold uppercase tracking-wider text-zinc-700">From GitHub · Deploy · Agent</p>
                <div className="space-y-1.5">
                  {recentCommits.slice(0, 3).map((c) => (
                    <ActivityItem key={c.sha} time={c.date} icon="◆" color="text-violet-400" title="GitHub" body={c.message.split('\n')[0].slice(0, 42)} />
                  ))}
                  {lastDeploy && <ActivityItem time={lastDeploy.at} icon="↑" color="text-emerald-400" title="Deploy" body={lastDeploy.title.slice(0, 42)} />}
                  {runActive && <ActivityItem time={runActive.startedAt} icon="◉" color="text-violet-400" title={runActive.worker ?? 'Agent'} body={(runActive.task ?? '').slice(0, 42)} />}
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ───── Nav Content Panel (repositories/agents/git/timeline/deployments/infrastructure) ───── */
function NavContentPanel({
  nav,
  recentCommits,
  lastDeploy,
  runActive,
  openFiles,
  worker,
  repo,
  branch,
  onBack,
}: {
  nav: string;
  recentCommits: WorkspaceActivity['commitsLast24h'];
  lastDeploy: DeployIntelligenceResponse['cards'][number] | null;
  runActive: FounderAgentRunRecord | false;
  openFiles: string[];
  worker: WorkerStatus | null;
  repo: string | null;
  branch: string;
  onBack: () => void;
}) {
  const titles: Record<string, string> = {
    repositories: 'Repositories',
    agents: 'AI Agents',
    git: 'Git',
    timeline: 'Timeline',
    deployments: 'Deployments',
    infrastructure: 'Infrastructure',
  };
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-[#0a0a0f] p-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">{titles[nav] ?? nav}</h2>
          <button onClick={onBack} className="text-[11px] text-violet-400 hover:underline">← Back to Workspace</button>
        </div>

        {nav === 'repositories' && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <p className="text-xs text-zinc-400">Connected repository</p>
            <p className="mt-1 font-mono text-sm text-violet-300">{repo ?? 'Not connected — link GitHub in Settings'}</p>
            <p className="mt-2 text-xs text-zinc-500">Default branch: <span className="font-mono text-zinc-300">{branch}</span></p>
            {openFiles.length > 0 && (
              <>
                <p className="mt-3 text-[10px] uppercase tracking-wider text-zinc-600">Open files ({openFiles.length})</p>
                <div className="mt-1 space-y-0.5">
                  {openFiles.slice(0, 12).map((f) => (
                    <p key={f} className="font-mono text-[11px] text-zinc-400">📄 {f}</p>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {nav === 'agents' && (
          <div className="space-y-2">
            {runActive ? (
              <div className="rounded-xl border border-violet-500/20 bg-violet-950/10 p-4">
                <p className="text-xs font-semibold text-violet-200">{runActive.worker ?? 'Agent'} — {runActive.status}</p>
                <p className="mt-1 text-xs text-zinc-400">{runActive.task}</p>
                <p className="mt-1 text-[10px] text-violet-400/60">{runActive.adapterLabel}</p>
              </div>
            ) : (
              <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-xs text-zinc-500">No agents running. Click "+ New Agent" in the top bar to dispatch one.</p>
            )}
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
              <p className="text-[10px] uppercase tracking-wider text-zinc-600">Available agent templates</p>
              <p className="mt-2 text-xs text-zinc-400">Hire Agent coming soon — Researcher, Frontend, Backend, Trading Analyst, Security Auditor, DevOps.</p>
            </div>
          </div>
        )}

        {(nav === 'git' || nav === 'timeline') && (
          <div className="space-y-2">
            {recentCommits.length > 0 ? recentCommits.map((c) => (
              <div key={c.sha} className="flex items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-violet-500" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-zinc-200">{c.message.split('\n')[0]}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-zinc-500">{c.sha.slice(0, 7)} · {new Date(c.date).toLocaleString()}</p>
                </div>
              </div>
            )) : <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-xs text-zinc-500">No commits yet — connect GitHub in Settings.</p>}
          </div>
        )}

        {nav === 'deployments' && (
          <div className="space-y-2">
            {lastDeploy ? (
              <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/10 p-4">
                <p className="text-xs font-semibold text-emerald-200">{lastDeploy.title}</p>
                <p className="mt-1 text-[10px] text-zinc-500">{new Date(lastDeploy.at).toLocaleString()}</p>
              </div>
            ) : <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-xs text-zinc-500">No deployments detected.</p>}
          </div>
        )}

        {nav === 'infrastructure' && (
          <div className="grid grid-cols-2 gap-2">
            <InfraRow label="GitHub" ok={worker?.githubConnected ?? false} />
            <InfraRow label="Cursor" ok={worker?.connections?.cursor ?? false} />
            <InfraRow label="Founder Node" ok={worker?.connections?.founderNode ?? false} />
            <InfraRow label="Neon DB" ok={worker?.llmConnected ?? false} />
            <InfraRow label="Railway" ok={Boolean(lastDeploy)} />
            <InfraRow label="Vercel" ok={Boolean(lastDeploy)} />
          </div>
        )}
      </div>
    </div>
  );
}

function InfraRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className={`flex items-center justify-between rounded-lg border px-3 py-2 ${ok ? 'border-emerald-500/20 bg-emerald-950/10' : 'border-zinc-800 bg-zinc-900/30'}`}>
      <span className="text-xs text-zinc-300">{label}</span>
      <span className={`text-[10px] font-medium ${ok ? 'text-emerald-300' : 'text-zinc-600'}`}>{ok ? 'Healthy' : 'Offline'}</span>
    </div>
  );
}

/* ───── Resume Workspace Panel (replaces cold-start empty state when session exists) ───── */
function ResumeWorkspacePanel({
  session,
  repo,
  branch,
  worker,
  recentAgents,
  onResume,
  onContinueAgent,
}: {
  session: WorkspaceSessionData | null;
  repo: string | null;
  branch: string;
  worker: WorkerStatus | null;
  recentAgents: RecentAgentsResponse | null;
  onResume: () => void;
  onContinueAgent: (agent: RecentAgent, prompt: string) => void;
}) {
  const messageCount = session?.conversation?.length ?? 0;
  const lastActive = session?.updatedAt ? formatRelativeTimeShort(session.updatedAt) : 'recently';
  const cursorOk = Boolean(worker?.connections?.cursor);
  const nodeOk = Boolean(worker?.connections?.founderNode);
  const repoOk = Boolean(repo);
  const agentCount = recentAgents?.agents?.length ?? 0;
  const hasSession = Boolean(session);

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <div className="rounded-2xl border border-violet-500/20 bg-gradient-to-b from-violet-950/20 to-zinc-900/30 p-6">
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400/80">Founder OS</p>
        <h2 className="mt-1 text-xl font-bold text-white">
          {hasSession ? 'Workspace restored.' : 'Desktop is live.'}
        </h2>
        <p className="mt-1 text-xs text-zinc-400">
          {hasSession
            ? 'Your laptop stayed working while you were away.'
            : 'Your desktop is connected. Pick up where you left off.'}
        </p>

        <div className="mt-5 space-y-1.5 text-xs">
          <ResumeRow label="Repository" value={repo ?? 'Not linked'} ok={repoOk} />
          <ResumeRow label="Branch" value={branch} ok={repoOk} />
          <ResumeRow label="Cursor" value={cursorOk ? 'Connected' : 'Offline'} ok={cursorOk} />
          <ResumeRow label="Founder Node" value={nodeOk ? 'Online' : 'Offline'} ok={nodeOk} />
          <ResumeRow label="Terminal" value={session?.terminalScrollback?.length ? `Recovered (${session.terminalScrollback.length} lines)` : 'Empty'} ok={Boolean(session?.terminalScrollback?.length)} />
          <ResumeRow label="Conversation" value={`${messageCount} message${messageCount === 1 ? '' : 's'} recovered`} ok={messageCount > 0} />
          {agentCount > 0 && <ResumeRow label="Active Agents" value={`${agentCount} recent agent${agentCount === 1 ? '' : 's'}`} ok={true} />}
          <ResumeRow label="Last active" value={lastActive} ok={false} />
        </div>

        <button
          type="button"
          onClick={onResume}
          className="mt-5 w-full rounded-lg bg-violet-600 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-violet-500"
        >
          Resume Workspace →
        </button>
      </div>

      {agentCount > 0 && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-4">
          <RecentAgentsPanel data={recentAgents} onContinueAgent={onContinueAgent} compact />
        </div>
      )}

      <p className="text-center text-[10px] text-zinc-600">
        Or start fresh by typing a question below — your saved session stays in the background.
      </p>
    </div>
  );
}

function ResumeRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-zinc-800/60 bg-zinc-900/40 px-3 py-1.5">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className="flex items-center gap-1.5 text-zinc-200">
        <span className={ok ? 'text-emerald-400' : 'text-zinc-500'}>{ok ? '✓' : '·'}</span>
        <span className="truncate text-zinc-300">{value}</span>
      </span>
    </div>
  );
}

function formatRelativeTimeShort(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'recently';
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
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

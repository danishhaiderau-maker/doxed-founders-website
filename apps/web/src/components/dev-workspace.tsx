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
  fetchFounderPlatformConnectionsHub,
  fetchConnectedWorkspaces,
  createConnectedWorkspace,
  deleteConnectedWorkspace,
  fetchCopilotSocialDraft,
  createBuildPost,
  syncGitHubCommits,
  type BuilderSettings,
  type DeployIntelligenceResponse,
  type DesktopBridgeResponse,
  type FounderAgentRunRecord,
  type FounderOnboardingStatus,
  type PlatformConnectionsHub,
  type RecentAgentsResponse,
  type RecentAgent,
  type ConnectedWorkspace,
} from '@/lib/api';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { useVoiceInput } from '@/hooks/use-voice-input';
import { VoiceWaveform } from '@/components/voice-waveform';
import { copilotAsk, copilotAskStream, type ContextCollectionStep } from '@/lib/api';
import {
  loadWorkspaceSession,
  saveWorkspaceSessionPatch,
  fetchConnectedWorkspaceSession,
  updateConnectedWorkspaceSession,
  type WorkspaceConversationMessage,
  type WorkspacePanelState,
  type WorkspaceSessionData,
  type WorkspaceTerminalLine,
} from '@/lib/api';
import { trimTerminalScrollback } from '@/lib/workspace-session';
import {
  emitEvent,
  advanceStream,
  clearStream,
  getEvents,
  resetFounderEventBus,
  useFounderEvents,
  type FounderEvent,
  type FounderEventCategory,
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
  isIDE?: boolean;
  comingSoon?: boolean;
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
  { key: 'OPENROUTER', label: 'OpenRouter', provider: 'OpenRouter', model: 'auto', contextWindow: 'varies', costPerMtokens: 'varies', strengths: 'Multi-model routing' },
  { key: 'SURPLUS', label: 'Surplus Intelligence', provider: 'Surplus', model: 'claude-opus-4.8', contextWindow: '200K', costPerMtokens: '$15', strengths: 'Premium reasoning · multi-agent' },
  { key: 'JATEVO', label: 'Jatevo Gateway', provider: 'Jatevo', model: 'auto', contextWindow: '128K', costPerMtokens: '$5', strengths: 'Cost-efficient gateway' },
  { key: 'PHALA', label: 'Phala TEE', provider: 'Phala', model: 'phala/deepseek-chat-v3-0324', contextWindow: '64K', costPerMtokens: '$2', strengths: 'Private inference · TEE attested' },
];

// IDEs & Build Agents — execution environments (not chat LLMs).
// These need their own API/connection key, separate from AI provider keys.
const IDE_MODELS: Omit<ModelInfo, 'connected' | 'promo'>[] = [
  { key: 'CURSOR', label: 'Cursor', provider: 'Cursor', model: 'cursor-agent', contextWindow: '200K', costPerMtokens: '$20/mo', strengths: 'Autonomous coding · PR creation · Cloud agent', isIDE: true },
  { key: 'OPENHANDS', label: 'OpenHands', provider: 'OpenHands', model: 'openhands', contextWindow: '—', costPerMtokens: '—', strengths: 'Self-hosted build agent · Open source', isIDE: true },
];

// Coming Soon IDEs/build agents — disabled, non-clickable placeholders.
const COMING_SOON_IDES: Omit<ModelInfo, 'connected' | 'promo'>[] = [
  { key: 'CLAUDE_CODE', label: 'Claude Code', provider: 'Anthropic', model: 'claude-code', contextWindow: '200K', costPerMtokens: '—', strengths: 'Terminal-native coding agent · Coming soon', isIDE: true, comingSoon: true },
  { key: 'VS_CODE', label: 'VS Code', provider: 'Microsoft', model: 'vscode', contextWindow: '—', costPerMtokens: '—', strengths: 'Editor-integrated build agent · Coming soon', isIDE: true, comingSoon: true },
  { key: 'WINDSURF', label: 'Windsurf', provider: 'Codeium', model: 'windsurf', contextWindow: '—', costPerMtokens: '—', strengths: 'Cascade agent · Coming soon', isIDE: true, comingSoon: true },
];

// Private / local inference — Founder Vault Memory is a placeholder for the
// in-progress on-device memory store.
const PRIVATE_EXTRA: Omit<ModelInfo, 'connected' | 'promo'>[] = [
  { key: 'VAULT_MEMORY', label: 'Founder Vault Memory', provider: 'Founder OS', model: 'vault-memory', contextWindow: 'varies', costPerMtokens: 'Free', strengths: 'On-device memory · Private recall', local: true, comingSoon: true },
];

// Category buckets for the runtime dropdown. Keys reference ALL_MODELS.
const BRAIN_KEYS = new Set(['OPENAI', 'OPENAI_THINKING', 'ANTHROPIC', 'GEMINI', 'GLM', 'DEEPSEEK', 'GROK']);
const MARKETPLACE_KEYS = new Set(['OPENROUTER', 'SURPLUS', 'JATEVO']);
const PRIVATE_KEYS = new Set(['OLLAMA', 'PHALA']);

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
  const [connectionsHub, setConnectionsHub] = useState<PlatformConnectionsHub | null>(null);
  const [loading, setLoading] = useState(true);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null);
  const [activeNav, setActiveNav] = useState<string>('workspace');
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState<
    { role: 'user' | 'agent'; text: string; model?: string; provider?: string; attachments?: { name: string }[] }[]
  >([]);
  const [liveContextSteps, setLiveContextSteps] = useState<ContextCollectionStep[] | null>(null);
  const [showContextPanel, setShowContextPanel] = useState(false);
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
  const [connectedWorkspaces, setConnectedWorkspaces] = useState<ConnectedWorkspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);
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
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
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
      fetchFounderPlatformConnectionsHub(accessToken),
      fetchConnectedWorkspaces(accessToken),
    ]);
    if (results[0].status === 'fulfilled') setActivity(results[0].value);
    if (results[1].status === 'fulfilled') setWorker(results[1].value);
    if (results[2].status === 'fulfilled') setSettings(results[2].value);
    if (results[3].status === 'fulfilled') setDeploys(results[3].value);
    if (results[4].status === 'fulfilled') setBridge(results[4].value);
    if (results[5].status === 'fulfilled') setActiveRun(results[5].value);
    if (results[6].status === 'fulfilled') setOnboarding(results[6].value);
    if (results[7].status === 'fulfilled') setRecentAgents(results[7].value);
    if (results[8].status === 'fulfilled') setConnectionsHub(results[8].value);
    if (results[9].status === 'fulfilled') setConnectedWorkspaces(results[9].value);
    setLoading(false);
  }, [accessToken]);

  useEffect(() => {
    load();
    const i = setInterval(load, 15_000);
    return () => clearInterval(i);
  }, [load]);

  // Debounced session persistence. Routes to the per-workspace session endpoint
  // (PUT /connected-workspace/:id/session) when an active workspace is selected,
  // and falls back to the legacy /workspace-session endpoint otherwise. The
  // eventLog (rolling 50 founder events) is folded into every flush so the
  // workspace timeline survives reloads.
  const pendingSessionPatchRef = useRef<Partial<WorkspaceSessionData>>({});
  const sessionSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionTokenRef = useRef(accessToken);
  sessionTokenRef.current = accessToken;
  const sessionWorkspaceRef = useRef(activeWorkspaceId);
  sessionWorkspaceRef.current = activeWorkspaceId;

  const flushSessionSave = useCallback(async () => {
    if (sessionSaveTimerRef.current) {
      clearTimeout(sessionSaveTimerRef.current);
      sessionSaveTimerRef.current = null;
    }
    const patch = pendingSessionPatchRef.current;
    pendingSessionPatchRef.current = {};
    if (!patch || Object.keys(patch).length === 0) return;
    const token = sessionTokenRef.current;
    if (!token) return;
    try {
      const wsId = sessionWorkspaceRef.current;
      const patchWithEvents = { ...patch, eventLog: getEvents().slice(-50) };
      if (wsId) {
        await updateConnectedWorkspaceSession(token, wsId, patchWithEvents);
      } else {
        await saveWorkspaceSessionPatch(token, patchWithEvents);
      }
    } catch {
      // best-effort — session save is fire-and-forget; UI still works offline
    }
  }, []);

  const saveSession = useCallback(
    (patch: Partial<WorkspaceSessionData>) => {
      pendingSessionPatchRef.current = { ...pendingSessionPatchRef.current, ...patch };
      if (sessionSaveTimerRef.current) clearTimeout(sessionSaveTimerRef.current);
      sessionSaveTimerRef.current = setTimeout(() => {
        void flushSessionSave();
      }, 500);
    },
    [flushSessionSave],
  );

  // Flush on unmount / page hide so the last patch isn't lost.
  useEffect(() => {
    const onHide = () => {
      void flushSessionSave();
    };
    window.addEventListener('beforeunload', onHide);
    window.addEventListener('pagehide', onHide);
    return () => {
      window.removeEventListener('beforeunload', onHide);
      window.removeEventListener('pagehide', onHide);
      void flushSessionSave();
    };
  }, [flushSessionSave]);

  // Load persisted workspace session once on mount → restore UI state. When an
  // active workspace was selected in a previous session (mirrored to localStorage
  // because the legacy session schema has no activeWorkspaceId column), load the
  // per-workspace session via GET /connected-workspace/:id/session; otherwise
  // fall back to the legacy /workspace-session endpoint.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!accessToken) return;
      let workspaceId: string | null = null;
      try {
        workspaceId = window.localStorage.getItem('dcf:active-workspace-id');
        if (workspaceId) setActiveWorkspaceId(workspaceId);
      } catch {
        // ignore storage errors
      }
      try {
        const session = workspaceId
          ? await fetchConnectedWorkspaceSession(accessToken, workspaceId)
          : await loadWorkspaceSession(accessToken);
        if (cancelled || !session) return;
        setRestoredSession(session);
        // Hydrate the founder event bus from the persisted rolling event log so
        // the Agent Event Timeline survives reloads. The bus has no setEvents()
        // helper, so we replay each event via emitEvent (best-effort).
        if (Array.isArray(session.eventLog) && session.eventLog.length > 0) {
          resetFounderEventBus();
          for (const ev of session.eventLog) {
            emitEvent(
              (ev.category as FounderEventCategory) ?? 'SYSTEM',
              ev.kind ?? 'restored',
              ev.message ?? '',
              {
                level: (ev.level as FounderEvent['level']) ?? 'info',
                stream: ev.stream,
                progress: ev.progress,
                meta: ev.meta,
              },
            );
          }
        }
        // Auto-restore conversation without a blocking resume modal when the
        // persisted session has one — set state directly and skip the panel.
        if (Array.isArray(session.conversation) && session.conversation.length > 0) {
          setChatMessages(session.conversation as typeof chatMessages);
          setResumeDismissed(true);
        }
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

  // Persist the active workspace selection to localStorage so it survives
  // reloads (the legacy session schema has no activeWorkspaceId column). The
  // initial restore from localStorage happens in the session-load effect above.
  useEffect(() => {
    try {
      if (activeWorkspaceId) {
        window.localStorage.setItem('dcf:active-workspace-id', activeWorkspaceId);
      } else {
        window.localStorage.removeItem('dcf:active-workspace-id');
      }
    } catch {
      // ignore storage errors
    }
  }, [activeWorkspaceId]);

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
  }, [settings, onboarding]);

  // IDEs & Build Agents — connection state comes from worker.connections
  // (Cursor desktop bridge) and worker.connections.openHands for OpenHands.
  const ideModels = useMemo<ModelInfo[]>(() => {
    return IDE_MODELS.map((m): ModelInfo => ({
      ...m,
      connected:
        m.key === 'CURSOR'
          ? Boolean(worker?.connections?.cursor)
          : m.key === 'OPENHANDS'
            ? Boolean(worker?.connections?.openHands)
            : false,
      promo: false,
    }));
  }, [worker]);

  useEffect(() => {
    if (!selectedModel) {
      const all = [...models, ...ideModels];
      if (all.length > 0) {
        setSelectedModel(models.find((m) => m.promo || m.connected) ?? models[0] ?? all[0]);
      }
    }
  }, [models, ideModels, selectedModel]);

  // When an IDE/build agent is the selected runtime, the second line of the
  // selector button shows the brain that will actually answer prompts.
  const activeBrain = useMemo<ModelInfo | null>(() => {
    if (selectedModel?.isIDE) {
      return models.find((m) => m.promo || m.connected) ?? models[0] ?? null;
    }
    return selectedModel ?? null;
  }, [selectedModel, models]);

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

  // Per-provider connection status from the Infrastructure Hub (real data, not proxies).
  const platformProvider = (key: string) => connectionsHub?.providers.find((p) => p.key === key) ?? null;
  const neonProvider = platformProvider('neon');
  const railwayProvider = platformProvider('railway');
  const vercelProvider = platformProvider('vercel');
  const ollamaReady = Boolean(settings?.founderNodeAi?.ollamaReady);

  function resumeWorkspace() {
    if (!restoredSession) {
      setResumeDismissed(true);
      return;
    }
    const s = restoredSession;
    if (s.selectedModelKey) {
      const matched = [...models, ...ideModels].find((m) => m.key === s.selectedModelKey);
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

  async function handleCreateWorkspace() {
    const label = window.prompt('Workspace name (e.g. "Founder OS redesign"):');
    if (!label?.trim()) return;
    try {
      const ws = await createConnectedWorkspace(accessToken, {
        label: label.trim(),
        repository: repo ?? undefined,
        branch: branch || undefined,
      });
      setConnectedWorkspaces((prev) => [ws, ...prev.filter((w) => w.id !== ws.id)]);
      setActiveWorkspaceId(ws.id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: `Could not create workspace: ${msg}`, model: 'System' },
      ]);
    }
  }

  async function handleSwitchWorkspace(workspaceId: string) {
    setActiveWorkspaceId(workspaceId);
    // The session will be loaded/switched via the workspace session API.
    // For now, just set the active workspace ID — the session persistence
    // will use this when saving/loading.
  }

  async function handleDeleteWorkspace(workspaceId: string) {
    if (!window.confirm('Delete this workspace?')) return;
    try {
      await deleteConnectedWorkspace(accessToken, workspaceId);
      setConnectedWorkspaces((prev) => prev.filter((w) => w.id !== workspaceId));
      setActiveWorkspaceId((cur) => (cur === workspaceId ? null : cur));
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: `Could not delete workspace: ${msg}`, model: 'System' },
      ]);
    }
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
        activeWorkspaceId,
      } as WorkspacePanelState,
      conversation: chatMessages as WorkspaceConversationMessage[],
    };
    saveSession(patch);
  }, [
    selectedModel,
    activeNav,
    terminalOpen,
    terminalHeight,
    sidebarOpen,
    chatMessages,
    saveSession,
    resumeDismissed,
    activeWorkspaceId,
  ]);

  // Save terminal scrollback every Nth line (avoid DB thrash on every line).
  useEffect(() => {
    if (!sessionAppliedRef.current) return;
    if (!resumeDismissed) return;
    if (terminalScrollback.length === 0) return;
    if (terminalScrollback.length % 10 !== 0) return; // every 10th line
    saveSession({ terminalScrollback: trimTerminalScrollback(terminalScrollback, 200) });
  }, [terminalScrollback, saveSession, resumeDismissed]);

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
    setLiveContextSteps(null);
    setShowContextPanel(false);

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

    // Try SSE streaming first — real context events + chunk streaming.
    // Falls back to the legacy POST path below if SSE is unavailable.
    try {
      let fullAnswer = '';
      let attributionProvider: string | undefined;
      let attributionModel: string | undefined;
      let contextSteps: ContextCollectionStep[] | undefined;

      setChatMessages((prev) => [
        ...prev,
        { role: 'agent', text: '', model: modelLabel },
      ]);

      for await (const event of copilotAskStream(accessToken, { prompt: text, provider: modelKey })) {
        if (event.type === 'context') {
          contextSteps = event.steps;
          setLiveContextSteps(event.steps);
          let progress = 0.12;
          for (const step of event.steps) {
            emitEvent(
              'SYSTEM',
              step.id,
              step.label,
              { stream, level: step.status === 'done' ? 'success' : 'info', progress },
            );
            progress = Math.min(progress + 0.08, 0.6);
          }
          advanceStream(stream, 0.6, `Context collected (${event.steps.length} steps)`);
        } else if (event.type === 'chunk') {
          fullAnswer += event.text;
          setChatMessages((prev) =>
            prev.map((m, idx) =>
              idx === prev.length - 1 && m.role === 'agent' ? { ...m, text: fullAnswer } : m,
            ),
          );
        } else if (event.type === 'attribution') {
          attributionProvider = event.provider;
          attributionModel = event.model ?? undefined;
        } else if (event.type === 'done') {
          fullAnswer = event.answer;
          const finalProvider = attributionProvider ?? event.answerProvider;
          const finalModel = attributionModel ?? finalProvider ?? modelLabel;
          setChatMessages((prev) =>
            prev.map((m, idx) =>
              idx === prev.length - 1 && m.role === 'agent'
                ? { ...m, text: fullAnswer, model: finalModel, provider: attributionProvider }
                : m,
            ),
          );
          emitEvent('AI', 'response_complete', `Answer delivered (${fullAnswer.length} chars)`, {
            stream,
            level: 'success',
            progress: 1,
            meta: { provider: finalProvider, sources: contextSteps?.map((s) => s.label) },
          });
          break;
        } else if (event.type === 'error') {
          throw new Error(event.message);
        }
      }

      if (!fullAnswer.trim()) {
        throw new Error('Empty SSE response');
      }
      setThinking(false);
      setTimeout(() => {
        clearStream(stream);
        setActiveStream((cur) => (cur === stream ? null : cur));
      }, 12000);
      return;
    } catch (sseError) {
      // SSE failed — remove the placeholder agent message and fall back to POST.
      setChatMessages((prev) => {
        if (prev.length > 0 && prev[prev.length - 1].role === 'agent' && !prev[prev.length - 1].text) {
          return prev.slice(0, -1);
        }
        return prev;
      });
      setLiveContextSteps(null);
      // Continue to legacy POST path below.
      void sseError;
    }

    // ---- Legacy POST fallback (fake context events + single-shot answer) ----
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
      if (result.contextCollection?.length) {
        setLiveContextSteps(result.contextCollection);
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
          <button
            type="button"
            onClick={() => {
              setActiveNav('workspace');
              setChatInput('Build: ');
              setTimeout(() => {
                chatInputRef.current?.focus();
                chatInputRef.current?.setSelectionRange(6, 6);
              }, 0);
              emitEvent('AGENT', 'new_agent', 'New agent ready — type your task below to dispatch it.', {
                stream: 'workspace',
                level: 'info',
              });
            }}
            className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500"
          >+ New Agent</button>
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

      {/* ═══ Status strip: repo · branch · desktop · IDE · brain · cost ═══ */}
      <StatusStrip
        repo={repo}
        branch={branch}
        desktopOnline={Boolean(bridge?.latest)}
        cursorConnected={Boolean(worker?.connections?.cursor)}
        selectedModelLabel={selectedModel?.label}
        todayCost={todayCost}
        savedVsCloud={savedVsCloud}
        usingLocal={usingLocal}
      />

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

          {/* Recent Workspaces — rich two-line entries mirroring desktop work */}
          <div className="flex min-h-0 shrink-0 flex-col border-t border-zinc-800/80">
            <div className="flex shrink-0 items-center justify-between px-3 py-1.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-zinc-600">Recent Workspaces</span>
              <button
                onClick={() => handleCreateWorkspace()}
                className="text-[9px] text-violet-400/80 hover:text-violet-300"
                title="New workspace"
              >+ New</button>
            </div>
            <div className="min-h-0 max-h-56 overflow-y-auto px-1.5 pb-1.5">
              {connectedWorkspaces.length === 0 ? (
                <p className="px-2 py-2 text-[10px] leading-relaxed text-zinc-600">
                  No workspaces yet — click <span className="text-violet-400">+ New</span> to create one
                </p>
              ) : (
                connectedWorkspaces.slice(0, 10).map((ws) => {
                  const status = workspaceStatus(ws.lastActiveAt);
                  const ide = ws.ideProvider ?? 'Unknown IDE';
                  const metaParts = [ide, ws.branch ?? '—', formatRelativeTime(ws.lastActiveAt)];
                  return (
                    <div
                      key={ws.id}
                      className={`group flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition ${
                        activeWorkspaceId === ws.id ? 'bg-violet-600/15 text-violet-300' : 'text-zinc-400 hover:bg-zinc-800/50'
                      }`}
                    >
                      <button
                        onClick={() => handleSwitchWorkspace(ws.id)}
                        className="flex min-w-0 flex-1 items-start gap-2 text-left"
                        title={ws.repository ?? ws.label}
                      >
                        <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${status.dot}`} title={status.label} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[11px] font-medium text-zinc-300">{ws.label}</p>
                          <p className="truncate text-[9px] text-zinc-600">
                            {metaParts.join(' · ')}
                          </p>
                        </div>
                      </button>
                      <button
                        onClick={() => handleDeleteWorkspace(ws.id)}
                        className="shrink-0 text-[9px] text-zinc-700 opacity-0 transition hover:text-red-400 group-hover:opacity-100"
                        title="Delete workspace"
                      >✕</button>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Active workspace indicator */}
          {activeWorkspaceId && (
            <div className="shrink-0 px-3 py-1 text-[9px] text-zinc-600">
              Active: {connectedWorkspaces.find((w) => w.id === activeWorkspaceId)?.label ?? 'Unknown'}
            </div>
          )}

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
            <div className="flex items-center gap-1.5 px-1 py-0.5 text-[9px] text-zinc-600">
              <span className="text-violet-500/80">●</span>
              <span>build in public</span>
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> All Systems Operational
            </div>
          </div>
        </aside>

        {/* ═══ Center: Founder Brain (~70%) + Terminal ═══ */}
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* Non-workspace nav panels */}
          {activeNav === 'social' && (
            <div className="min-h-0 flex-1 overflow-y-auto bg-[#0a0a0f]">
              <WorkspacePublishPanel
                accessToken={accessToken}
                activity={activity}
                deploys={deploys}
                onPublished={() => { void load(); }}
              />
              {socialPanel}
            </div>
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
              className="flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-left text-zinc-200 hover:border-violet-500/50">
              <span className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${selectedModel?.promo ? 'bg-amber-400' : selectedModel?.local ? 'bg-emerald-400' : selectedModel?.connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
              <div className="min-w-0 flex flex-col leading-tight">
                <span className="truncate text-xs font-medium text-zinc-100">
                  {selectedModel?.label ?? 'Select runtime'}
                </span>
                <span className="truncate text-[9px] text-zinc-500">
                  {selectedModel
                    ? selectedModel.isIDE
                      ? `${activeBrain?.label ?? 'No brain'} · ${activeBrain?.local ? 'Local' : 'Cloud'}`
                      : `${selectedModel.local ? 'Local' : 'Cloud'} · ${selectedModel.promo ? 'Promo' : selectedModel.connected ? 'Connected' : selectedModel.comingSoon ? 'Coming Soon' : 'Not connected'}`
                    : 'No runtime selected'}
                </span>
              </div>
              <ChevronIcon />
            </button>

            {/* AI Model Dropdown — 5 sections: Execution · Brains · Marketplace · Private · Admin Promo */}
            {modelDropdownOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setModelDropdownOpen(false)} />
                <div className="absolute left-3 top-12 z-40 w-80 rounded-xl border border-zinc-700 bg-[#12121a] shadow-2xl">
                  <div className="max-h-[400px] overflow-y-auto py-1">
                    {/* ── Section 1: EXECUTION (IDEs / build agents) ── */}
                    <DropdownSectionHeader label="Execution" hint="IDEs · build agents" />
                    {ideModels.map((m) => (
                      <DropdownModelRow key={m.key} m={m} selectedKey={selectedModel?.key} onSelect={(mm) => { setSelectedModel(mm); setModelDropdownOpen(false); }} />
                    ))}
                    {COMING_SOON_IDES.map((m) => (
                      <DropdownModelRow key={m.key} m={m as ModelInfo} selectedKey={selectedModel?.key} onSelect={() => {}} disabled />
                    ))}

                    {/* ── Section 2: BRAINS (cloud LLM providers) ── */}
                    <DropdownDivider />
                    <DropdownSectionHeader label="Brains" hint="cloud LLM providers" />
                    {models.filter((m) => BRAIN_KEYS.has(m.key)).map((m) => (
                      <DropdownModelRow key={m.key} m={m} selectedKey={selectedModel?.key} onSelect={(mm) => { setSelectedModel(mm); setModelDropdownOpen(false); }} />
                    ))}

                    {/* ── Section 3: MARKETPLACE (third-party aggregators) ── */}
                    <DropdownDivider />
                    <DropdownSectionHeader label="Marketplace" hint="aggregators · routing" />
                    {models.filter((m) => MARKETPLACE_KEYS.has(m.key)).map((m) => (
                      <DropdownModelRow key={m.key} m={m} selectedKey={selectedModel?.key} onSelect={(mm) => { setSelectedModel(mm); setModelDropdownOpen(false); }} />
                    ))}

                    {/* ── Section 4: PRIVATE (local / private inference) ── */}
                    <DropdownDivider />
                    <DropdownSectionHeader label="Private" hint="local · TEE · on-device" />
                    {models.filter((m) => PRIVATE_KEYS.has(m.key)).map((m) => (
                      <DropdownModelRow key={m.key} m={m} selectedKey={selectedModel?.key} onSelect={(mm) => { setSelectedModel(mm); setModelDropdownOpen(false); }} />
                    ))}
                    {PRIVATE_EXTRA.map((m) => (
                      <DropdownModelRow key={m.key} m={m as ModelInfo} selectedKey={selectedModel?.key} onSelect={() => {}} disabled />
                    ))}

                    {/* ── Section 5: ADMIN PROMO (always visible) ── */}
                    <DropdownDivider />
                    <DropdownSectionHeader label="Admin Promo" hint="always on · founder benefit" />
                    <AdminPromoRow
                      glmModel={models.find((m) => m.key === 'GLM') ?? null}
                      daysRemaining={onboarding?.promo?.daysRemaining ?? null}
                      eligible={Boolean(onboarding?.promo?.eligible)}
                      selected={selectedModel?.key === 'GLM'}
                      onSelect={() => {
                        const glm = models.find((m) => m.key === 'GLM');
                        if (glm) { setSelectedModel(glm); setModelDropdownOpen(false); }
                      }}
                    />
                  </div>
                  <div className="border-t border-zinc-800 px-3 py-2 text-[9px] text-zinc-600">
                    Brains need an AI API key · Execution needs its own IDE key — connect either in Settings → Builder
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
                  {chatMessages.map((msg, i) => {
                    const isLastAgent =
                      msg.role === 'agent' &&
                      i === chatMessages.length - 1;
                    const showPanelHere = isLastAgent && liveContextSteps && liveContextSteps.length > 0;
                    return (
                    <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${msg.role === 'user' ? 'bg-violet-600 text-white' : 'bg-zinc-800 text-zinc-200'}`}>
                        {msg.model && <p className="mb-1 text-[9px] font-semibold opacity-60">{msg.model}</p>}
                        {showPanelHere && (
                          <div className="mb-2 rounded-md border border-zinc-700/60 bg-zinc-900/40">
                            <button
                              onClick={() => setShowContextPanel(!showContextPanel)}
                              className="flex w-full items-center gap-1.5 px-2 py-1 text-[9px] text-zinc-400 hover:text-zinc-200"
                            >
                              <span>{showContextPanel ? '▾' : '▸'}</span>
                              <span>Live scan ({liveContextSteps!.length} steps)</span>
                            </button>
                            {showContextPanel && (
                              <div className="px-2 pb-1.5">
                                {liveContextSteps!.map((step, si) => (
                                  <div key={si} className="flex items-center gap-1.5 py-0.5 text-[9px] text-zinc-500">
                                    <span className={step.status === 'done' ? 'text-emerald-500/70' : 'text-zinc-600'}>
                                      {step.status === 'done' ? '✓' : '○'}
                                    </span>
                                    <span>{step.label}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                        {msg.attachments && msg.attachments.length > 0 && (
                          <div className="mb-1.5 flex flex-wrap gap-1">
                            {msg.attachments.map((a, j) => (
                              <span key={j} className="rounded bg-white/15 px-1.5 py-0.5 text-[9px]">📎 {a.name}</span>
                            ))}
                          </div>
                        )}
                        <p className="whitespace-pre-wrap">{msg.text}</p>
                        {msg.role === 'agent' && (msg.provider || msg.model) && msg.text && (
                          <div className="mt-1 flex items-center gap-1 text-[9px] text-zinc-500">
                            <span>🧠</span>
                            {msg.model && <span>{msg.model}</span>}
                            {msg.provider && msg.model && <span className="text-zinc-700">·</span>}
                            {msg.provider && <span>{msg.provider}</span>}
                          </div>
                        )}
                      </div>
                    </div>
                    );
                  })}
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
                <textarea ref={chatInputRef} value={chatInput} onChange={(e) => setChatInput(e.target.value)}
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
              <IntegrationCard
                label="Neon"
                status={neonProvider ? (neonProvider.connected ? 'Connected' : 'Not connected') : '—'}
                ok={Boolean(neonProvider?.connected)}
              />
              <IntegrationCard
                label="Railway"
                status={railwayProvider ? (railwayProvider.connected ? 'Connected' : 'Not connected') : '—'}
                ok={Boolean(railwayProvider?.connected)}
              />
              <IntegrationCard
                label="Vercel"
                status={vercelProvider ? (vercelProvider.connected ? 'Connected' : 'Not connected') : '—'}
                ok={Boolean(vercelProvider?.connected)}
              />
              <IntegrationCard
                label="Ollama"
                status={ollamaReady ? 'Running' : 'Off'}
                ok={ollamaReady}
              />
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

// Richer relative time for the Recent Workspaces sidebar — produces strings
// like "2 min ago", "1 hour ago", "3 days ago".
function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min === 1) return '1 min ago';
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr === 1) return '1 hour ago';
  if (hr < 24) return `${hr} hours ago`;
  const day = Math.floor(hr / 24);
  if (day === 1) return '1 day ago';
  if (day < 30) return `${day} days ago`;
  const mo = Math.floor(day / 30);
  if (mo === 1) return '1 month ago';
  if (mo < 12) return `${mo} months ago`;
  const yr = Math.floor(day / 365);
  return yr === 1 ? '1 year ago' : `${yr} years ago`;
}

// Workspace status dot color based on lastActiveAt.
// green = Running (<5 min), yellow = Waiting (<1 hr), gray = Dormant (older).
function workspaceStatus(lastActiveAt: string): { dot: string; label: string } {
  const then = new Date(lastActiveAt).getTime();
  if (Number.isNaN(then)) return { dot: 'bg-zinc-600', label: 'Dormant' };
  const diffMs = Date.now() - then;
  if (diffMs < 5 * 60_000) return { dot: 'bg-emerald-400', label: 'Running' };
  if (diffMs < 60 * 60_000) return { dot: 'bg-amber-400', label: 'Waiting' };
  return { dot: 'bg-zinc-600', label: 'Dormant' };
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

/* ───── Runtime dropdown helpers ───── */
function DropdownSectionHeader({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="px-3 py-1 text-[8px] font-semibold uppercase tracking-wider text-zinc-600">
      {label}
      {hint && <span className="ml-1.5 font-normal normal-case text-zinc-700">· {hint}</span>}
    </div>
  );
}

function DropdownDivider() {
  return <div className="mx-3 my-1 border-t border-zinc-800/60" />;
}

function DropdownModelRow({
  m,
  selectedKey,
  onSelect,
  disabled,
}: {
  m: ModelInfo;
  selectedKey?: string;
  onSelect: (m: ModelInfo) => void;
  disabled?: boolean;
}) {
  const selected = selectedKey === m.key;
  const isIDE = Boolean(m.isIDE);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => { if (!disabled) onSelect(m); }}
      className={`flex w-full items-start gap-2.5 px-3 py-2 text-left transition ${
        disabled
          ? 'cursor-not-allowed opacity-50'
          : selected
            ? 'bg-violet-600/10 hover:bg-violet-600/15'
            : 'hover:bg-zinc-800/50'
      }`}
    >
      <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
        m.comingSoon
          ? 'bg-zinc-700'
          : m.promo
            ? 'bg-amber-400'
            : m.local
              ? 'bg-emerald-400'
              : m.connected
                ? 'bg-emerald-400'
                : 'bg-zinc-600'
      }`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`text-xs font-medium ${disabled ? 'text-zinc-500' : 'text-zinc-200'}`}>{m.label}</span>
          {isIDE && <span className="rounded bg-sky-500/15 px-1 py-0.5 text-[8px] font-semibold text-sky-300">IDE</span>}
          {m.promo && <span className="rounded bg-amber-500/15 px-1 py-0.5 text-[8px] font-semibold text-amber-300">{m.promoLabel ?? 'Promo'}</span>}
          {m.local && <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[8px] font-semibold text-emerald-300">Free · Local</span>}
          {!m.comingSoon && m.connected && !m.promo && !m.local && (
            <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[8px] font-semibold text-emerald-300">Connected</span>
          )}
          {!m.comingSoon && !m.connected && !isIDE && !m.local && !m.promo && (
            <span className="rounded bg-zinc-700/60 px-1 py-0.5 text-[8px] font-semibold text-zinc-400">Missing key</span>
          )}
          {!m.comingSoon && isIDE && !m.connected && (
            <span className="rounded bg-zinc-700/60 px-1 py-0.5 text-[8px] font-semibold text-zinc-400">Connect in Settings</span>
          )}
          {m.comingSoon && (
            <span className="rounded bg-zinc-800 px-1 py-0.5 text-[8px] font-semibold text-zinc-500">Coming Soon</span>
          )}
          {selected && <span className="ml-auto text-violet-400">✓</span>}
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[9px] text-zinc-500">
          <span>{m.provider}</span><span>·</span><span>{m.contextWindow} ctx</span><span>·</span><span>{m.costPerMtokens}{!isIDE && !m.local ? '/M' : ''}</span>
        </div>
        <p className="mt-0.5 text-[9px] text-zinc-600">{m.strengths}</p>
      </div>
    </button>
  );
}

function AdminPromoRow({
  glmModel,
  daysRemaining,
  eligible,
  selected,
  onSelect,
}: {
  glmModel: ModelInfo | null;
  daysRemaining: number | null;
  eligible: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const label = glmModel?.label ?? 'GLM 5.2';
  const daysLabel = daysRemaining != null ? `${daysRemaining} days remaining` : eligible ? 'Active' : 'Activate promo';
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full items-start gap-2.5 px-3 py-2 text-left transition ${
        selected ? 'bg-amber-500/10 hover:bg-amber-500/15' : 'hover:bg-zinc-800/50'
      }`}
    >
      <span className="mt-1 text-[11px] text-amber-400">⭐</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-medium text-zinc-200">{label}</span>
          <span className="rounded bg-amber-500/15 px-1 py-0.5 text-[8px] font-semibold text-amber-300">Admin Promo</span>
          {selected && <span className="ml-auto text-amber-400">✓</span>}
        </div>
        <p className="mt-0.5 text-[9px] text-amber-300/70">{daysLabel}</p>
        <p className="mt-0.5 text-[9px] text-zinc-600">Founder benefit · always visible regardless of provider config</p>
      </div>
    </button>
  );
}

/* ───── Status strip: real-time connection + cost bar under the commit timeline ───── */
function StatusStrip({
  repo,
  branch,
  desktopOnline,
  cursorConnected,
  selectedModelLabel,
  todayCost,
  savedVsCloud,
  usingLocal,
}: {
  repo: string | null;
  branch: string;
  desktopOnline: boolean;
  cursorConnected: boolean;
  selectedModelLabel?: string;
  todayCost: number;
  savedVsCloud: number;
  usingLocal: boolean | undefined;
}) {
  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-zinc-800/80 bg-[#0d0d14] px-3 py-1 text-[10px] text-zinc-500">
      {repo && (
        <span className="flex items-center gap-1">
          <span className="text-zinc-400">{repo}</span>
          {branch && <span className="text-zinc-600">·</span>}
          {branch && <span className="text-violet-400/70">{branch}</span>}
        </span>
      )}

      <span className="flex items-center gap-1">
        <span className={`h-1.5 w-1.5 rounded-full ${desktopOnline ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
        <span>{desktopOnline ? 'Desktop Online' : 'Desktop Offline'}</span>
      </span>

      <span className="flex items-center gap-1">
        <span className={`h-1.5 w-1.5 rounded-full ${cursorConnected ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
        <span>{cursorConnected ? 'IDE Connected' : 'IDE Disconnected'}</span>
      </span>

      {selectedModelLabel && (
        <span className="flex items-center gap-1">
          <span className="text-zinc-600">Brain:</span>
          <span className="text-zinc-400">{selectedModelLabel}</span>
        </span>
      )}

      <span className="ml-auto flex items-center gap-1">
        <span className="text-zinc-600">Today:</span>
        <span className="text-emerald-400">${todayCost.toFixed(2)}</span>
        <span className="text-zinc-600">·</span>
        <span className="text-emerald-400/70">Saved ${savedVsCloud.toFixed(2)}</span>
        {usingLocal && <span className="text-[9px] text-zinc-600">local/promo</span>}
      </span>
    </div>
  );
}

/* ───── Workspace Publish Panel: compact build-in-public side panel ─────
 * Sits at the top of the Social nav panel. Surfaces today's engineering
 * activity (commits, deployments, issues closed) and wires the existing
 * founder-social-hub publish infrastructure (fetchCopilotSocialDraft +
 * createBuildPost + ShareOnX) so every workspace can build in public
 * without leaving the IDE. Reuses API functions — no duplication. */
function WorkspacePublishPanel({
  accessToken,
  activity,
  deploys,
  onPublished,
}: {
  accessToken: string;
  activity: WorkspaceActivity | null;
  deploys: DeployIntelligenceResponse | null;
  onPublished: () => void;
}) {
  const origin = useShareOrigin();
  const events = useFounderEvents();

  const [headline, setHeadline] = useState('');
  const [body, setBody] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [busyKind, setBusyKind] = useState<'x' | 'devlog' | 'changelog' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const commitsToday = activity?.commitsLast24h?.length ?? 0;
  const deploysToday = useMemo(() => {
    const cards = deploys?.cards ?? [];
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return cards.filter((c) => new Date(c.at).getTime() >= cutoff).length;
  }, [deploys]);
  const issuesClosedToday = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const seen = new Set<string>();
    let count = 0;
    for (const e of events) {
      if (e.ts < cutoff) continue;
      if (e.category !== 'GITHUB' && e.category !== 'GIT') continue;
      const hay = `${e.kind} ${e.message}`.toLowerCase();
      if (!hay.includes('closed') && !hay.includes('issue-closed')) continue;
      if (seen.has(e.id)) continue;
      seen.add(e.id);
      count += 1;
    }
    return count;
  }, [events]);

  const ready = commitsToday > 0 || deploysToday > 0 || issuesClosedToday > 0;
  const canPublish = Boolean(headline.trim() && body.trim()) && !publishing;

  async function generate(kind: 'x' | 'devlog' | 'changelog') {
    if (!accessToken) return;
    setDrafting(true);
    setBusyKind(kind);
    setError(null);
    setNotice(null);
    try {
      await syncGitHubCommits(accessToken).catch(() => undefined);
      const result = await fetchCopilotSocialDraft(undefined, accessToken, {
        audience: kind === 'devlog' ? 'developer' : 'trader',
      });
      const h = result.headline || "Today's build update";
      setHeadline(h);
      if (kind === 'x') {
        setBody(result.tweetVersion ?? result.xHook ?? h);
      } else if (kind === 'devlog') {
        setBody(result.developerSummary ?? result.displayBody ?? result.body);
      } else {
        const sections = [
          result.whatShipped ? `## What shipped\n${result.whatShipped}` : '',
          result.whyItMatters ? `## Why it matters\n${result.whyItMatters}` : '',
          result.whatUsersNotice ? `## What users notice\n${result.whatUsersNotice}` : '',
          result.whatsNext ? `## What's next\n${result.whatsNext}` : '',
        ].filter(Boolean);
        setBody(sections.length > 0 ? sections.join('\n\n') : (result.displayBody ?? result.body));
      }
      setNotice(
        result.fallback
          ? 'Drafted from project context (connect an LLM in AI Stack for richer output)'
          : `${kind === 'x' ? 'X post' : kind === 'devlog' ? 'Dev log' : 'Changelog'} drafted — edit before publishing`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Draft failed');
    } finally {
      setDrafting(false);
      setBusyKind(null);
    }
  }

  async function publish() {
    if (!canPublish) return;
    setPublishing(true);
    setError(null);
    setNotice(null);
    try {
      await createBuildPost({ headline: headline.trim(), body: body.trim() }, accessToken);
      setHeadline('');
      setBody('');
      setNotice('Published to feed');
      onPublished();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Publish failed');
    } finally {
      setPublishing(false);
    }
  }

  const shareUrl = origin;

  return (
    <div className="border-b border-zinc-800/80 bg-[#0a0a0f] px-4 py-4">
      {/* Section 1: Today's Work Summary */}
      <section>
        <h2 className="text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
          Today&apos;s Work
        </h2>
        <div className="mt-2 rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-3 py-2.5">
          {ready ? (
            <div className="space-y-1 text-[10px] text-zinc-300">
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">commits</span>
                <span className="font-medium text-zinc-200">{commitsToday}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">deployments</span>
                <span className="font-medium text-zinc-200">{deploysToday}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">issues closed</span>
                <span className="font-medium text-zinc-200">{issuesClosedToday}</span>
              </div>
              <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-violet-300">
                <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />
                Ready to publish
              </div>
            </div>
          ) : (
            <p className="text-[10px] text-zinc-600">No activity today yet.</p>
          )}
        </div>
      </section>

      {/* Section 2 + 3: Publish Actions + Draft preview */}
      <section className="mt-4">
        <h2 className="text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
          Publish
        </h2>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={drafting}
            onClick={() => void generate('x')}
            className="rounded-md bg-zinc-800 px-3 py-1.5 text-[10px] text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
          >
            {busyKind === 'x' ? 'Generating…' : 'Generate X Post'}
          </button>
          <button
            type="button"
            disabled={drafting}
            onClick={() => void generate('devlog')}
            className="rounded-md bg-zinc-800 px-3 py-1.5 text-[10px] text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
          >
            {busyKind === 'devlog' ? 'Generating…' : 'Generate Dev Log'}
          </button>
          <button
            type="button"
            disabled={drafting}
            onClick={() => void generate('changelog')}
            className="rounded-md bg-zinc-800 px-3 py-1.5 text-[10px] text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
          >
            {busyKind === 'changelog' ? 'Generating…' : 'Generate Changelog'}
          </button>
          <button
            type="button"
            disabled={!canPublish}
            onClick={() => void publish()}
            className="rounded-md bg-violet-600/80 px-3 py-1.5 text-[10px] text-white hover:bg-violet-600 disabled:opacity-50"
          >
            {publishing ? 'Publishing…' : 'Publish'}
          </button>
        </div>

        {(headline.trim() || body.trim()) && (
          <div className="mt-3 space-y-2">
            <input
              value={headline}
              onChange={(e) => setHeadline(e.target.value)}
              placeholder="Headline — what shipped today?"
              className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-violet-500/50"
            />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Edit your update before publishing…"
              rows={5}
              className="w-full resize-none rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-violet-500/50"
            />
            <div className="flex flex-wrap items-center gap-2">
              <ShareOnXButton
                text={headline.trim() ? `${headline.trim()}\n${body.trim()}` : body.trim()}
                url={shareUrl}
                label="Share to X"
                className={!canPublish ? 'pointer-events-none opacity-40' : ''}
              />
              {notice && <span className="text-[10px] text-emerald-300/90">{notice}</span>}
            </div>
          </div>
        )}

        {error && <p className="mt-2 text-[10px] text-red-300">{error}</p>}
        {!headline.trim() && !body.trim() && !error && (
          <p className="mt-2 text-[10px] text-zinc-600">
            Tap a generate button to draft from today&apos;s commits and deploys, then publish.
          </p>
        )}
      </section>
    </div>
  );
}



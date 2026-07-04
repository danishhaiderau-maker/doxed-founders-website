'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  connectAiProvider,
  connectCursorCloud,
  connectGitHubRepo,
  copilotAskStream,
  type CopilotMissingConnection,
  fetchAvailableBrains,
  fetchConnectedWorkspaces,
  fetchDesktopBridge,
  type BridgeMessage,
  fetchIdeBridgeSessionMessages,
  fetchIdeBridgeSessions,
  fetchIdeBridgeWorkspaces,
  fetchRecentAgents,
  dispatchToIdeSession,
  type BridgeSession,
  type ConnectedWorkspace,
  type DesktopBridgeResponse,
  type RecentAgent,
} from '@/lib/api';

type ChatMsg = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  provider?: string;
  pending?: boolean;
  thinking?: boolean;
  missingConnections?: CopilotMissingConnection[];
};

type BrainOption = { key: string; label: string; hint: string };

import { CollapsibleInfo } from '@/components/ui/collapsible-info';
import { FOUNDER_NODE_GITHUB_RELEASES } from '@/components/founder-node-downloads';

const FOUNDER_DEN_ONBOARD_URL = '/founder-den?onboard=sovereign#founder-node-download';

const BYOK_STORAGE_KEY = 'dcf.byok.apiKey';
const BYOK_BRAIN: BrainOption = { key: 'BYOK', label: 'Bring Your Own Key', hint: 'Paste Z.ai / OpenAI key' };
const RULE_BASED_BRAIN: BrainOption = { key: 'RULE_BASED', label: 'Rule-based', hint: 'Free fallback' };

type IdeOption = { key: string; label: string };

const IDES: IdeOption[] = [
  { key: 'cursor', label: 'Cursor' },
  { key: 'vscode', label: 'VS Code' },
  { key: 'windsurf', label: 'Windsurf' },
  { key: 'openhands', label: 'OpenHands' },
  { key: 'claude_code', label: 'Claude Code' },
];

type IdeWorkspace = {
  id: string;
  title: string;
  repository?: string;
  branch?: string;
  ideProvider: string;
  lastActiveAt: string;
  hasActiveAgent?: boolean;
  messageCount?: number;
};

type WorkspaceItem = {
  id: string;
  label: string;
  sub: string;
  source: 'cursor' | 'bridge' | 'connected';
  ideProvider?: string | null;
  branch?: string | null;
  agentStatus?: string | null;
  lastActive?: string;
};

type Props = {
  accessToken: string;
  socialPanel?: React.ReactNode;
  settingsPanel?: React.ReactNode;
  initialCopilotPrompt?: string | null;
  onInitialCopilotPromptConsumed?: () => void;
};

function timeAgo(iso?: string | null): string {
  if (!iso || iso === 'never') return 'never';
  const ms = new Date(iso).getTime();
  if (!Number.isFinite(ms)) return 'never';
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 0) return 'just now';
  if (s < 60) return s + ' sec ago';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' hr ago';
  return Math.floor(s / 86400) + ' day ago';
}

function SessionRow({
  session,
  selected,
  onSelect,
  dotClass,
}: {
  session: BridgeSession;
  selected: boolean;
  onSelect: (s: BridgeSession) => void;
  dotClass: string;
}) {
  const repoLabel = [session.repository, session.branch].filter(Boolean).join(' · ');
  return (
    <button
      type='button'
      onClick={() => onSelect(session)}
      className={
        'mb-0.5 block w-full rounded-lg px-3 py-2 text-left transition ' +
        (selected ? 'bg-violet-500/15 ring-1 ring-violet-400/30' : 'hover:bg-white/5')
      }
    >
      <div className='flex items-center gap-2'>
        <span className={'h-1.5 w-1.5 shrink-0 rounded-full ' + dotClass} />
        <span className='truncate text-sm font-medium text-zinc-100'>{session.title}</span>
      </div>
      {session.subtitle && <div className='mt-0.5 truncate pl-3.5 text-xs text-zinc-500'>{session.subtitle}</div>}
      {repoLabel && <div className='mt-0.5 truncate pl-3.5 text-[0.65rem] text-zinc-600'>{repoLabel}</div>}
    </button>
  );
}

const HISTORY_MSG_COLLAPSED_LEN = 280;

function HistoryMessage({ msg }: { msg: BridgeMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isUser = msg.role === 'user';
  const text = msg.content || '';
  const overLimit = text.length > HISTORY_MSG_COLLAPSED_LEN;
  const shown = expanded || !overLimit ? text : text.slice(0, HISTORY_MSG_COLLAPSED_LEN) + '…';
  const timeLabel = msg.timestamp ? timeAgo(msg.timestamp) : null;
  return (
    <div className={'flex ' + (isUser ? 'justify-end' : 'justify-start')}>
      <div className={'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ' + (isUser ? 'bg-violet-500/15 text-violet-50' : 'bg-white/5 text-zinc-100')}>
        <div className='mb-1 flex items-center gap-2 text-[0.65rem] uppercase tracking-wider text-zinc-500'>
          <span>{isUser ? 'You' : msg.role === 'system' ? 'System' : 'Assistant'}</span>
          {msg.model && <span className='text-zinc-600'>· {msg.model}</span>}
          {timeLabel && <span className='text-zinc-600'>· {timeLabel}</span>}
        </div>
        <p className='whitespace-pre-wrap break-words'>{shown}</p>
        {overLimit && (
          <button
            type='button'
            onClick={() => setExpanded((v) => !v)}
            className='mt-1 text-[0.65rem] text-violet-300/80 hover:text-violet-200'
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        )}
      </div>
    </div>
  );
}

function HistoryThread({
  messages,
  loading,
  error,
}: {
  messages: BridgeMessage[] | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading && (!messages || messages.length === 0)) {
    return (
      <div className='mb-4 rounded-lg border border-white/5 bg-white/[0.03] px-3 py-4 text-center text-xs text-zinc-500'>
        Loading conversation…
      </div>
    );
  }
  if (error) {
    return (
      <div className='mb-4 rounded-lg border border-rose-400/20 bg-rose-500/[0.06] px-3 py-2 text-xs text-rose-300'>
        Couldn&apos;t load messages: {error}
      </div>
    );
  }
  if (!messages || messages.length === 0) {
    return (
      <div className='mb-4 rounded-lg border border-white/5 bg-white/[0.03] px-3 py-4 text-center text-xs text-zinc-500'>
        No messages stored for this session yet.
      </div>
    );
  }
  return (
    <div className='mx-auto mb-4 max-w-3xl space-y-2.5'>
      <div className='px-1 text-[0.65rem] font-semibold uppercase tracking-wider text-zinc-500'>
        Conversation history
      </div>
      {messages.map((m, i) => (
        <HistoryMessage key={i} msg={m} />
      ))}
    </div>
  );
}

export function MinimalDevWorkspace({
  accessToken,
  socialPanel,
  settingsPanel,
  initialCopilotPrompt,
  onInitialCopilotPromptConsumed,
}: Props) {
  const [bridge, setBridge] = useState<DesktopBridgeResponse | null>(null);
  const [ideWorkspaces, setIdeWorkspaces] = useState<IdeWorkspace[]>([]);
  const [connected, setConnected] = useState<ConnectedWorkspace[]>([]);
  const [agents, setAgents] = useState<RecentAgent[]>([]);
  const [sessions, setSessions] = useState<BridgeSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [history, setHistory] = useState<BridgeMessage[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [nodeStatus, setNodeStatus] = useState({ desktopOnline: false, cursorConnected: false, founderNodeOnline: false });
  const [brains, setBrains] = useState<BrainOption[] | null>(null);
  const [byokKey, setByokKey] = useState<string>('');
  const [byokDraft, setByokDraft] = useState<string>('');
  const [selectedBrain, setSelectedBrain] = useState<string>('GLM');
  const [selectedIde, setSelectedIde] = useState<string>('cursor');
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedWsId, setSelectedWsId] = useState<string | null>(null);
  const [fullScreenPanel, setFullScreenPanel] = useState<'none' | 'social' | 'settings'>('none');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [lastSync, setLastSync] = useState<string>('never');
  const [dispatchNotice, setDispatchNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const firedInitial = useRef(false);

  const refresh = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [b, iw, c, a, ss] = await Promise.all([
        fetchDesktopBridge(accessToken).catch(() => null),
        fetchIdeBridgeWorkspaces(accessToken).catch(() => [] as IdeWorkspace[]),
        fetchConnectedWorkspaces(accessToken).catch(() => [] as ConnectedWorkspace[]),
        fetchRecentAgents(accessToken).catch(() => null),
        fetchIdeBridgeSessions(accessToken).catch(() => [] as BridgeSession[]),
      ]);
      if (b) { setBridge(b); setLastSync(b.latest?.updatedAt || 'never'); }
      if (iw) setIdeWorkspaces(iw);
      if (c) setConnected(c);
      if (a) { setAgents(a.agents); setNodeStatus({ desktopOnline: a.desktopOnline, cursorConnected: a.cursorConnected, founderNodeOnline: a.founderNodeOnline }); }
      if (ss) setSessions(ss);
      setError(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load workspace state';
      setError(msg);
    }
  }, [accessToken]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  // Load BYOK key from localStorage (never sent to the DB) and fetch the
  // dynamic brain list from the API (reflects which promo keys are configured).
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(BYOK_STORAGE_KEY);
      if (stored && stored.trim().length >= 8) setByokKey(stored.trim());
    } catch {
      // localStorage may be unavailable (private mode) — BYOK just won't persist.
    }
    if (!accessToken) return;
    let cancelled = false;
    fetchAvailableBrains(accessToken)
      .then((available) => {
        if (cancelled || !Array.isArray(available)) return;
        const opts: BrainOption[] = available
          .filter((b) => b.available)
          .map((b) => ({ key: b.key, label: b.label, hint: b.hint }));
        // Always append BYOK so users with their own Z.ai/OpenAI key can use it.
        if (!opts.some((b) => b.key === 'BYOK')) opts.push(BYOK_BRAIN);
        setBrains(opts);
        // If the currently-selected brain isn't in the new list, fall back to
        // the first real provider or RULE_BASED.
        setSelectedBrain((curr) => {
          if (opts.some((b) => b.key === curr)) return curr;
          const firstReal = opts.find((b) => b.key !== 'RULE_BASED' && b.key !== 'BYOK');
          return firstReal?.key ?? 'RULE_BASED';
        });
      })
      .catch(() => {
        if (cancelled) return;
        // Endpoint unreachable — degrade to rule-based only so chat still works.
        setBrains([RULE_BASED_BRAIN, BYOK_BRAIN]);
        setSelectedBrain((curr) => (curr === 'GLM' ? 'RULE_BASED' : curr));
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (firedInitial.current) return;
    if (!initialCopilotPrompt?.trim()) return;
    firedInitial.current = true;
    setInput(initialCopilotPrompt);
    onInitialCopilotPromptConsumed?.();
  }, [initialCopilotPrompt, onInitialCopilotPromptConsumed]);

  const workspaces: WorkspaceItem[] = useMemo(() => {
    const items: WorkspaceItem[] = [];
    const seen = new Set<string>();
    for (const iw of ideWorkspaces) {
      const id = 'cursor:' + iw.id;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({ id, label: iw.title, sub: iw.branch ? iw.branch : (iw.repository ? (iw.repository.split('/').pop() || iw.repository) : iw.ideProvider), source: 'cursor', ideProvider: iw.ideProvider, branch: iw.branch, agentStatus: iw.hasActiveAgent ? 'running' : undefined, lastActive: iw.lastActiveAt });
    }
    if (bridge?.nodes?.length) {
      for (const n of bridge.nodes) {
        const id = 'bridge:' + n.nodeId;
        if (seen.has(id)) continue;
        seen.add(id);
        items.push({ id, label: n.taskLabel || n.label || 'Cursor workspace', sub: n.branch ? 'branch: ' + n.branch : 'live', source: 'bridge', branch: n.branch, agentStatus: n.agentStatus, lastActive: n.updatedAt });
      }
    }
    for (const c of connected) {
      const id = 'ws:' + c.id;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({ id, label: c.label, sub: c.repository || c.ideProvider || 'workspace', source: 'connected', ideProvider: c.ideProvider, branch: c.branch, lastActive: c.lastActiveAt });
    }
    return items;
  }, [ideWorkspaces, bridge, connected]);

  const isNodeLive = nodeStatus.founderNodeOnline || !!bridge?.latest || !!bridge?.nodes?.length;
  const cursorConnected = nodeStatus.cursorConnected;
  const agentCount = agents.length;
  const resolvedBrains = brains ?? [RULE_BASED_BRAIN];
  const brainLabel = resolvedBrains.find((b) => b.key === selectedBrain)?.label || selectedBrain;
  const realBrainsAvailable = resolvedBrains.some((b) => b.key !== 'RULE_BASED' && b.key !== 'BYOK');
  const byokConfigured = byokKey.trim().length >= 8;
  const repoFullName =
    ideWorkspaces.find((w) => w.repository?.trim())?.repository ||
    connected.find((c) => c.repository?.trim())?.repository ||
    null;
  const repoName =
    (repoFullName ? (repoFullName.split('/').pop() || repoFullName) : null) ||
    bridge?.latest?.taskLabel ||
    'Not detected';
  const branchName =
    bridge?.latest?.branch ||
    ideWorkspaces.find((w) => w.branch?.trim())?.branch ||
    connected.find((c) => c.branch?.trim())?.branch ||
    'unknown';

  // Group Cursor chat sessions by their owning workspace so the sidebar
  // reads like WhatsApp's conversation list (chats grouped per workspace)
  // rather than a flat task list. Cursor sessions carry a `workspaceId`
  // matching the UUID in the workspaces list's `cursor:<uuid>` id; sessions
  // we can't match fall back to their repository folder name, then to a
  // catch-all "Other chats" bucket.
  const sessionsByWorkspace = useMemo<
    Array<{ key: string; label: string; sessions: BridgeSession[] }>
  >(() => {
    const wsByUuid = new Map<string, IdeWorkspace>();
    for (const w of ideWorkspaces) {
      if (w.ideProvider !== 'cursor') continue;
      const uuid = w.id.startsWith('cursor:') ? w.id.slice('cursor:'.length) : w.id;
      wsByUuid.set(uuid, w);
    }
    const groups = new Map<string, { label: string; sessions: BridgeSession[] }>();
    for (const s of sessions) {
      let key: string;
      let label: string;
      const ws = s.workspaceId ? wsByUuid.get(s.workspaceId) : undefined;
      if (ws) {
        key = 'ws:' + ws.id;
        label = ws.title;
      } else if (s.repository) {
        const repoName = s.repository.split('/').pop() || s.repository;
        key = 'repo:' + repoName;
        label = repoName;
      } else {
        key = 'other';
        label = 'Other chats';
      }
      const g = groups.get(key);
      if (g) g.sessions.push(s);
      else groups.set(key, { label, sessions: [s] });
    }
    for (const g of groups.values()) {
      g.sessions.sort(
        (a, b) => new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
      );
    }
    return Array.from(groups.entries())
      .map(([key, g]) => ({ key, ...g }))
      .sort((a, b) => {
        const aTop = a.sessions[0]?.lastActiveAt || '';
        const bTop = b.sessions[0]?.lastActiveAt || '';
        return new Date(bTop).getTime() - new Date(aTop).getTime();
      });
  }, [sessions, ideWorkspaces]);

  const [collapsedWorkspaces, setCollapsedWorkspaces] = useState<Set<string>>(new Set());
  const toggleWorkspace = useCallback((key: string) => {
    setCollapsedWorkspaces((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const selectedSession = sessions.find((s) => s.id === selectedSessionId) ?? null;

  // Load the conversation thread whenever the user picks a session. We show
  // whatever was already embedded in the session list immediately (no network
  // wait), then refresh from the dedicated endpoint so the user sees the
  // latest messages even if the heartbeat payload was truncated.
  useEffect(() => {
    if (!selectedSessionId || !accessToken) {
      setHistory(null);
      setHistoryError(null);
      return;
    }
    let cancelled = false;
    const embedded = selectedSession?.messages ?? null;

    setHistory(embedded);
    setHistoryError(null);
    setHistoryLoading(true);

    fetchIdeBridgeSessionMessages(accessToken, selectedSessionId)
      .then((msgs) => {
        if (cancelled) return;
        if (Array.isArray(msgs)) setHistory(msgs);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setHistoryError(e instanceof Error ? e.message : 'Failed to load messages');
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedSessionId, accessToken, selectedSession?.messages]);

  const handleSelectSession = useCallback(
    (session: BridgeSession) => {
      setSelectedSessionId(session.id);
      setSidebarOpen(false);
      // Don't prefill the input with a technical summary — the conversation
      // thread above shows the full history; the user just continues typing
      // as if replying in a chat app.
      setInput('');
    },
    [],
  );

  const handleSend = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    const userMsg: ChatMsg = { id: 'u-' + Date.now(), role: 'user', text: prompt };
    const assistantId = 'a-' + Date.now();
    const assistantMsg: ChatMsg = { id: assistantId, role: 'assistant', text: '', pending: true, thinking: true };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    setInput('');
    setBusy(true);
    setError(null);

    // If a Cursor chat session is selected in the sidebar, also queue the
    // prompt for relay to the local Cursor IDE via Founder Node. Fire and
    // forget — the AI brain response below is the primary feedback; the
    // dispatch just makes Cursor start working on it locally.
    const sessionForDispatch = selectedSessionId
      ? sessions.find((s) => s.id === selectedSessionId)
      : null;
    const isCursorSession =
      selectedSessionId &&
      (sessionForDispatch?.ideProvider === 'cursor' || sessionForDispatch?.ideProvider == null);
    if (accessToken && isCursorSession && sessionForDispatch) {
      setDispatchNotice('Queued for local Cursor — Founder Node will type it in shortly.');
      void dispatchToIdeSession(accessToken, sessionForDispatch.id, prompt, 'cursor')
        .then(() => {
          setDispatchNotice('Sent to Cursor via Founder Node.');
          setTimeout(() => setDispatchNotice(null), 6000);
        })
        .catch((e: unknown) => {
          const msg = e instanceof Error ? e.message : 'Failed to queue for Cursor';
          setDispatchNotice(`Cursor relay failed: ${msg}`);
          setTimeout(() => setDispatchNotice(null), 8000);
        });
    }

    let firstTokenSeen = false;
    let providerLabel = selectedBrain;

    const patch = (mut: (msg: ChatMsg) => ChatMsg) =>
      setMessages((m) => m.map((msg) => (msg.id === assistantId ? mut(msg) : msg)));

    try {
      const effectiveProvider = selectedBrain === 'BYOK' ? 'BYOK' : selectedBrain;
      await copilotAskStream(
        prompt,
        accessToken,
        {
          provider: effectiveProvider,
          userApiKey: selectedBrain === 'BYOK' ? byokKey : null,
        },
        {
          onAttribution: ({ provider }) => {
            providerLabel = provider;
            patch((msg) => ({ ...msg, provider }));
          },
          onToken: (text) => {
            if (!firstTokenSeen) {
              firstTokenSeen = true;
              patch((msg) => ({ ...msg, thinking: false, text }));
            } else {
              patch((msg) => ({ ...msg, thinking: false, text: msg.text + text }));
            }
          },
          onDone: ({ answer, answerProvider, missingConnections, llmErrors }) => {
            patch((msg) => ({
              ...msg,
              pending: false,
              thinking: false,
              provider: answerProvider || providerLabel,
              text: answer || msg.text || 'No response.',
              missingConnections: missingConnections?.length ? missingConnections : undefined,
            }));
            if (llmErrors?.length && !answer) {
              setError(llmErrors.join(', '));
            }
          },
          onError: (message) => {
            patch((msg) => ({
              ...msg,
              pending: false,
              thinking: false,
              text: msg.text || ('Warning: ' + message),
            }));
            setError(message);
          },
        },
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Chat request failed';
      patch((m2) => ({ ...m2, pending: false, thinking: false, text: m2.text || ('Warning: ' + msg) }));
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [input, busy, accessToken, selectedBrain, selectedSessionId, sessions, byokKey]);

  const selectedWs = workspaces.find((w) => w.id === selectedWsId) ?? null;
  const showConnectWizard = !isNodeLive && workspaces.length === 0;

  return (
    <div className='flex h-[calc(100vh-3.5rem)] w-full bg-[#08080c] text-zinc-100'>
      {/* Mobile sidebar toggle */}
      <button
        onClick={() => setSidebarOpen((v) => !v)}
        className='fixed left-3 top-16 z-50 flex h-9 w-9 items-center justify-center rounded-lg bg-violet-600/90 text-white shadow-lg md:hidden'
        aria-label='Toggle sidebar'
      >
        {sidebarOpen ? '✕' : '☰'}
      </button>
      {/* Mobile backdrop */}
      {sidebarOpen && (
        <div
          className='fixed inset-0 z-30 bg-black/60 md:hidden'
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`absolute inset-y-0 left-0 z-40 flex w-72 shrink-0 flex-col border-r border-white/5 bg-[#0a0a0f] transition-transform duration-200 md:static md:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
        <div className='flex items-center justify-between px-4 py-3'>
          <span className='text-xs font-semibold uppercase tracking-wider text-zinc-400'>Workspaces</span>
          <button onClick={refresh} className='text-xs text-zinc-500 hover:text-zinc-200' aria-label='Refresh'>retry</button>
        </div>
        <div className='min-h-0 flex-1 overflow-y-auto px-2 pb-3'>
          {workspaces.length === 0 && <div className='px-3 py-6 text-center text-xs text-zinc-600'>No workspaces detected. Make sure Founder Node v0.7.3+ is running and Cursor is open.</div>}
          {workspaces.map((w) => (
            <button key={w.id} onClick={() => setSelectedWsId(w.id)} className={'mb-1 block w-full rounded-lg px-3 py-2.5 text-left transition ' + (selectedWsId === w.id ? 'bg-white/10 ring-1 ring-white/15' : 'hover:bg-white/5')}>
              <div className='flex items-center gap-2'>
                <span className={'h-1.5 w-1.5 shrink-0 rounded-full ' + (w.source === 'cursor' ? 'bg-purple-400' : w.source === 'bridge' ? 'bg-emerald-400' : 'bg-zinc-500')} />
                <span className='truncate text-sm font-medium text-zinc-100'>{w.label}</span>
              </div>
              <div className='mt-0.5 truncate pl-3.5 text-xs text-zinc-500'>{w.sub}</div>
              {w.agentStatus && <div className='mt-0.5 pl-3.5 text-xs text-emerald-400/80'>- {w.agentStatus}</div>}
            </button>
          ))}

          {sessions.length > 0 && (
            <div className='mt-3'>
              <div className='px-3 pb-1 pt-1 text-[0.65rem] font-semibold uppercase tracking-wider text-zinc-500'>Cursor Chats</div>
              {sessionsByWorkspace.map((g) => {
                const collapsed = collapsedWorkspaces.has(g.key);
                return (
                  <div key={g.key} className='mb-1'>
                    <button
                      type='button'
                      onClick={() => toggleWorkspace(g.key)}
                      className='flex w-full items-center gap-1.5 rounded-md px-3 py-1.5 text-left transition hover:bg-white/5'
                    >
                      <span
                        className={
                          'text-[0.6rem] text-zinc-500 transition-transform ' +
                          (collapsed ? '' : 'rotate-90')
                        }
                      >
                        ▶
                      </span>
                      <span className='truncate text-[0.7rem] font-semibold uppercase tracking-wider text-zinc-300'>
                        {g.label}
                      </span>
                      <span className='ml-auto rounded-full bg-white/5 px-1.5 text-[0.6rem] text-zinc-500'>
                        {g.sessions.length}
                      </span>
                    </button>
                    {!collapsed && (
                      <div className='ml-1'>
                        {g.sessions.map((s) => (
                          <SessionRow
                            key={g.key + ':' + s.id}
                            session={s}
                            selected={selectedSessionId === s.id}
                            onSelect={handleSelectSession}
                            dotClass={
                              Date.now() - new Date(s.lastActiveAt).getTime() < 86400000
                                ? 'bg-emerald-400'
                                : 'bg-violet-400'
                            }
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <div className='border-t border-white/5 px-2 py-2'>
          <button onClick={() => setFullScreenPanel('social')} className='mb-1 block w-full rounded-md px-3 py-1.5 text-left text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200'>Show social</button>
          <button onClick={() => setFullScreenPanel('settings')} className='block w-full rounded-md px-3 py-1.5 text-left text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200'>Show settings</button>
        </div>
      </aside>

      <main className='flex min-w-0 flex-1 flex-col'>
        {/* Today card */}
        <div className='border-b border-white/5 bg-[#0a0a0f] px-4 py-3'>
          <div className='mx-auto max-w-3xl'>
            <div className='mb-2 flex items-center gap-3'>
              <span className='text-xs font-semibold uppercase tracking-wider text-zinc-400'>Today</span>
              <div className='ml-auto flex items-center gap-2'>
                <select value={selectedIde} onChange={(e) => setSelectedIde(e.target.value)} className='rounded-md border border-white/10 bg-[#12121a] px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:ring-1 focus:ring-violet-400/40'>
                  {IDES.map((ide) => (<option key={ide.key} value={ide.key}>{ide.label}</option>))}
                </select>
                <select value={selectedBrain} onChange={(e) => setSelectedBrain(e.target.value)} className='rounded-md border border-white/10 bg-[#12121a] px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:ring-1 focus:ring-emerald-400/40'>
                  {resolvedBrains.map((b) => (<option key={b.key} value={b.key}>{b.label} - {b.hint}</option>))}
                </select>
              </div>
            </div>
            <div className='grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-3'>
              <div className='flex items-center gap-2'><span className={'h-2 w-2 rounded-full ' + (isNodeLive ? 'bg-emerald-400' : 'bg-zinc-600')} /><span className='text-zinc-500'>Founder Node</span><span className='ml-auto text-zinc-200'>{isNodeLive ? 'Online' : 'Offline'}</span></div>
              <div className='flex items-center gap-2'><span className={'h-2 w-2 rounded-full ' + (cursorConnected ? 'bg-emerald-400' : 'bg-zinc-600')} /><span className='text-zinc-500'>Cursor</span><span className='ml-auto text-zinc-200'>{cursorConnected ? 'Connected' : 'Not connected'}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Repository</span><span className='ml-auto truncate text-zinc-200'>{repoName}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Branch</span><span className='ml-auto text-zinc-200'>{branchName}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>AI</span><span className='ml-auto text-emerald-400'>{brainLabel}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Active Agents</span><span className='ml-auto text-zinc-200'>{agentCount}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Last Sync</span><span className='ml-auto text-zinc-400'>{timeAgo(lastSync)}</span></div>
            </div>
            {!realBrainsAvailable && (
              <div className='mt-3 rounded-lg border border-amber-400/20 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200/90'>
                No AI key configured — using free fallback. Pick <span className='font-semibold'>Bring Your Own Key</span> below to paste a Z.ai/OpenAI key, or ask an admin to enable the promo pool.
              </div>
            )}
            {selectedBrain === 'BYOK' && (
              <div className='mt-3 rounded-lg border border-violet-400/20 bg-violet-500/[0.06] px-3 py-2.5 text-xs'>
                <div className='flex flex-wrap items-center gap-2'>
                  <span className='font-medium text-violet-200'>Your API key (Z.ai / OpenAI-compatible)</span>
                  {byokConfigured && <span className='rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-300'>Saved in browser</span>}
                </div>
                <p className='mt-1 text-zinc-400'>Stored locally in your browser only — never sent to the Founder OS database. Get a GLM 5.2 key from <a href='https://z.ai' target='_blank' rel='noreferrer' className='text-violet-300 underline hover:text-violet-200'>z.ai</a>; the OpenAI-compatible endpoint is <code className='text-zinc-300'>https://api.z.ai/api/coding/paas/v4</code>.</p>
                <div className='mt-2 flex flex-wrap items-center gap-2'>
                  <input
                    type='password'
                    value={byokDraft}
                    onChange={(e) => setByokDraft(e.target.value)}
                    placeholder={byokConfigured ? 'Enter a new key to replace…' : 'Paste your Z.ai / OpenAI key'}
                    className='min-w-0 flex-1 rounded-lg border border-white/10 bg-[#12121a] px-3 py-2 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-violet-400/40'
                  />
                  <button
                    type='button'
                    disabled={!byokDraft.trim()}
                    onClick={() => {
                      const v = byokDraft.trim();
                      if (v.length < 8) return;
                      try { window.localStorage.setItem(BYOK_STORAGE_KEY, v); } catch { /* private mode */ }
                      setByokKey(v);
                      setByokDraft('');
                    }}
                    className='rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-violet-500 disabled:opacity-40'
                  >
                    Save key
                  </button>
                  {byokConfigured && (
                    <button
                      type='button'
                      onClick={() => {
                        try { window.localStorage.removeItem(BYOK_STORAGE_KEY); } catch { /* private mode */ }
                        setByokKey('');
                        setByokDraft('');
                      }}
                      className='rounded-lg bg-white/5 px-3 py-2 text-xs text-zinc-300 transition hover:bg-white/10'
                    >
                      Clear
                    </button>
                  )}
                </div>
                {!byokConfigured && (
                  <p className='mt-2 text-[0.65rem] text-amber-300/80'>Paste and save a key before sending — BYOK routes your prompt directly through your key.</p>
                )}
              </div>
            )}
          </div>
        </div>

        {showConnectWizard && (
          <div className='flex flex-col items-center justify-center gap-4 px-6 py-12 text-center'>
            <div className='max-w-md'>
              <h2 className='text-lg font-semibold text-zinc-100'>Connect your IDE</h2>
              <p className='mt-2 text-sm text-zinc-400'>To see your Cursor workspaces here, install Founder Node v0.7.3+ on your laptop. It automatically detects your Cursor sessions and streams them here.</p>
              <div className='mt-4 space-y-2'>
                <a
                  href={FOUNDER_DEN_ONBOARD_URL}
                  className='inline-flex w-full items-center justify-center rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500'
                >
                  Download Founder Node v0.7.3 — setup hub
                </a>
                <a
                  href={FOUNDER_NODE_GITHUB_RELEASES}
                  target='_blank'
                  rel='noreferrer'
                  className='block text-center text-xs text-cyan-400/80 underline hover:text-cyan-300'
                >
                  Or download directly from GitHub releases
                </a>
              </div>
              <CollapsibleInfo title='Setup steps' hint='Pair & open Cursor' accent='emerald'>
                <ol className='space-y-2 text-left text-sm text-zinc-300'>
                  <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'>
                    <span className='font-semibold text-emerald-400'>1.</span> Download and install Founder Node from the links above.
                  </li>
                  <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'>
                    <span className='font-semibold text-emerald-400'>2.</span> Pair it with your account using the pairing code in the Founder Node tray menu (no sign-in required).
                  </li>
                  <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'>
                    <span className='font-semibold text-emerald-400'>3.</span> Open Cursor — workspaces appear here automatically.
                  </li>
                </ol>
              </CollapsibleInfo>
              <p className='mt-3 text-xs text-zinc-500'>Meanwhile, chat below works with any Brain - no Cursor key needed.</p>
            </div>
          </div>
        )}

        <div ref={scrollRef} className='min-h-0 flex-1 overflow-y-auto px-4 py-4'>
          {selectedWs && !selectedSession && <div className='mb-3 rounded-lg border border-white/5 bg-white/[0.03] px-3 py-2 text-xs text-zinc-400'>Working in: <span className='text-zinc-200'>{selectedWs.label}</span>{selectedWs.branch && <span className='text-zinc-500'> - {selectedWs.branch}</span>}</div>}
          {selectedSession && (
            <div className='mx-auto mb-3 max-w-3xl rounded-lg border border-violet-400/20 bg-violet-500/[0.06] px-3 py-2 text-xs text-zinc-300'>
              <div className='flex items-center gap-2'>
                <span
                  className={
                    'h-1.5 w-1.5 shrink-0 rounded-full ' +
                    (Date.now() - new Date(selectedSession.lastActiveAt).getTime() < 86400000
                      ? 'bg-emerald-400'
                      : 'bg-violet-400')
                  }
                />
                <span className='text-zinc-500'>Continuing chat:</span>
                <span className='font-medium text-zinc-100'>{selectedSession.title}</span>
                {selectedSession.isAgentProject && (
                  <span className='rounded bg-violet-500/20 px-1.5 py-0.5 text-[0.6rem] uppercase tracking-wider text-violet-300'>
                    Agent
                  </span>
                )}
                <span className='ml-auto text-zinc-500'>{timeAgo(selectedSession.lastActiveAt)}</span>
              </div>
            </div>
          )}
          {selectedSession && (
            <HistoryThread messages={history} loading={historyLoading} error={historyError} />
          )}
          {messages.length === 0 && !showConnectWizard && <div className='flex h-full items-center justify-center text-sm text-zinc-600'>Ask anything. Founder OS will route your request to the right Brain and dispatch to your IDE.</div>}
          <div className='mx-auto max-w-3xl space-y-3'>
            {messages.map((m) => (
              <div key={m.id} className={'flex ' + (m.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div className={'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ' + (m.role === 'user' ? 'bg-emerald-500/15 text-emerald-50' : 'bg-white/5 text-zinc-100')}>
                  <p className='whitespace-pre-wrap break-words'>
                    {m.text || (m.thinking ? 'Thinking…' : m.pending ? '…' : '')}
                    {m.thinking && <span className='ml-1 inline-block animate-pulse text-zinc-500'>▋</span>}
                  </p>
                  {m.provider && !m.pending && m.role === 'assistant' && <p className='mt-1 text-xs text-zinc-500'>via {m.provider}</p>}
                  {m.role === 'assistant' && !m.pending && m.missingConnections && m.missingConnections.length > 0 && (
                    <InlineOnboarding
                      connections={m.missingConnections}
                      accessToken={accessToken}
                      onConnected={() => {
                        setMessages((prev) =>
                          prev.map((msg) =>
                            msg.id === m.id ? { ...msg, missingConnections: undefined } : msg,
                          ),
                        );
                        refresh();
                      }}
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className='border-t border-white/5 bg-[#0a0a0f] px-4 py-3'>
          {error && <div className='mb-2 text-xs text-rose-400'>{error}</div>}
          {dispatchNotice && (
            <div className='mb-2 text-xs text-emerald-400/90'>→ {dispatchNotice}</div>
          )}
          <div className='mx-auto flex max-w-3xl items-end gap-2'>
            <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }} rows={1} placeholder='Message Founder OS - shift+enter for newline' className='min-h-[44px] flex-1 resize-none rounded-xl border border-white/10 bg-[#12121a] px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-400/40' />
            <button onClick={handleSend} disabled={busy || !input.trim()} className='rounded-xl bg-emerald-500 px-4 py-3 text-sm font-semibold text-white transition hover:bg-emerald-400 disabled:opacity-40'>{busy ? '...' : 'Send'}</button>
          </div>
        </div>
      </main>

      {fullScreenPanel === 'social' && socialPanel && (
        <div className='fixed inset-0 z-50 flex flex-col bg-[#08080c]'>
          <div className='flex items-center justify-between border-b border-white/5 px-4 py-3'>
            <span className='text-sm font-semibold text-zinc-200'>Social</span>
            <button onClick={() => setFullScreenPanel('none')} className='rounded-md bg-white/5 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/10'>Hide social</button>
          </div>
          <div className='min-h-0 flex-1 overflow-y-auto p-4'>{socialPanel}</div>
        </div>
      )}
      {fullScreenPanel === 'settings' && settingsPanel && (
        <div className='fixed inset-0 z-50 flex flex-col bg-[#08080c]'>
          <div className='flex items-center justify-between border-b border-white/5 px-4 py-3'>
            <span className='text-sm font-semibold text-zinc-200'>Settings</span>
            <button onClick={() => setFullScreenPanel('none')} className='rounded-md bg-white/5 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/10'>Hide settings</button>
          </div>
          <div className='min-h-0 flex-1 overflow-y-auto p-4'>{settingsPanel}</div>
        </div>
      )}
    </div>
  );
}

/**
 * Inline onboarding panel rendered under an assistant message when the backend
 * reports missing connections (GitHub, AI key, Cursor). Shows ONE thing at a
 * time — the first missing connection — with a conversational prompt and an
 * input to paste the key / repo. On submit, calls the matching connect API.
 */
function InlineOnboarding({
  connections,
  accessToken,
  onConnected,
}: {
  connections: CopilotMissingConnection[];
  accessToken: string;
  onConnected: () => void;
}) {
  const first = connections[0];
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!first) return null;

  const placeholder =
    first.action === 'connect_github'
      ? 'owner/repo (e.g. danishhaiderau-maker/doxed-founders-website)'
      : first.action === 'connect_ai'
        ? 'Paste your AI API key (GLM / DeepSeek / OpenAI …)'
        : first.action === 'connect_cursor'
          ? 'Paste your Cursor Cloud API key'
          : '';

  const providerKey = 'GLM';

  const submit = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      if (first.action === 'connect_github') {
        await connectGitHubRepo(v, accessToken);
        setMsg(`Linked ${v}. I can now read your commits and push code.`);
      } else if (first.action === 'connect_ai') {
        await connectAiProvider(providerKey.toLowerCase(), v, accessToken);
        setMsg('AI key saved. GLM is now your brain — ask me anything.');
      } else if (first.action === 'connect_cursor') {
        await connectCursorCloud(v, accessToken);
        setMsg('Cursor Cloud connected. I can now dispatch code tasks to your IDE.');
      } else {
        setErr('Open Founder Node on your machine to continue.');
      }
      setValue('');
      onConnected();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Could not save that — try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className='mt-3 rounded-xl border border-emerald-400/20 bg-emerald-500/[0.06] px-3 py-2.5 text-xs'>
      <div className='flex items-center gap-2'>
        <span className='h-1.5 w-1.5 rounded-full bg-emerald-400' />
        <span className='font-medium text-emerald-200'>{first.label} not connected</span>
      </div>
      <p className='mt-1 text-zinc-300'>{first.detail}</p>
      {first.action !== 'open_founder_node' && (
        <div className='mt-2 flex items-end gap-2'>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={placeholder}
            type={first.action === 'connect_ai' || first.action === 'connect_cursor' ? 'password' : 'text'}
            className='min-w-0 flex-1 rounded-lg border border-white/10 bg-[#12121a] px-3 py-2 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-400/40'
          />
          <button
            onClick={submit}
            disabled={busy || !value.trim()}
            className='rounded-lg bg-emerald-500 px-3 py-2 text-xs font-semibold text-white transition hover:bg-emerald-400 disabled:opacity-40'
          >
            {busy ? 'Saving…' : 'Connect'}
          </button>
        </div>
      )}
      {connections.length > 1 && (
        <p className='mt-2 text-[0.65rem] text-zinc-500'>
          +{connections.length - 1} more to set up later.
        </p>
      )}
      {msg && <p className='mt-2 text-emerald-300'>{msg}</p>}
      {err && <p className='mt-2 text-rose-400'>{err}</p>}
    </div>
  );
}
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  connectAiProvider,
  connectCursorCloud,
  connectGitHubRepo,
  copilotAskStream,
  type CopilotMissingConnection,
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

const BRAINS: BrainOption[] = [
  { key: 'GLM', label: 'GLM 5.2', hint: 'Promo - fast' },
  { key: 'DEEPSEEK', label: 'DeepSeek V3', hint: 'Platform fallback' },
  { key: 'ANTHROPIC', label: 'Claude', hint: 'Anthropic' },
  { key: 'OPENAI', label: 'GPT', hint: 'OpenAI' },
  { key: 'GEMINI', label: 'Gemini', hint: 'Google' },
  { key: 'OLLAMA', label: 'Ollama', hint: 'Local - Founder Node' },
  { key: 'OPENROUTER', label: 'OpenRouter', hint: 'Multi-model' },
  { key: 'PHALA', label: 'PHA', hint: 'Phala Network' },
  { key: 'JATEVO', label: 'JATEVO', hint: 'Jatevo AI' },
  { key: 'SURPLUS', label: 'Surplus', hint: 'Surplus Compute' },
];

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
    <div className='mb-4 space-y-2.5'>
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
  const brainLabel = BRAINS.find((b) => b.key === selectedBrain)?.label || selectedBrain;
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

  // Bucket Cursor chat sessions by recency for the sidebar grouping.
  // "Today" = since local midnight; "Last 7 Days" = within 7 days excl. today.
  const sessionBuckets = useMemo<{ today: BridgeSession[]; last7: BridgeSession[] }>(() => {
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const sevenDaysAgo = now.getTime() - 7 * 24 * 60 * 60 * 1000;
    const today: BridgeSession[] = [];
    const last7: BridgeSession[] = [];
    for (const s of sessions) {
      const ts = new Date(s.lastActiveAt).getTime();
      if (!Number.isFinite(ts)) continue;
      if (ts >= startOfToday) today.push(s);
      else if (ts >= sevenDaysAgo) last7.push(s);
    }
    return { today, last7 };
  }, [sessions]);

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
      const lines: string[] = [];
      lines.push(`Continuing Cursor session: ${session.title}`);
      if (session.subtitle) lines.push(`Last work: ${session.subtitle}`);
      const repoLabel = session.repository || session.branch;
      if (repoLabel) {
        const parts: string[] = [];
        if (session.repository) parts.push(session.repository);
        if (session.branch) parts.push(session.branch);
        lines.push(`Repo: ${parts.join(' · ')}`);
      }
      const add = session.totalLinesAdded;
      const rem = session.totalLinesRemoved;
      const files = session.filesChangedCount;
      if (typeof files === 'number' || typeof add === 'number' || typeof rem === 'number') {
        lines.push(
          `Diff: ${typeof files === 'number' ? files + ' file(s)' : ''}${
            typeof add === 'number' || typeof rem === 'number'
              ? ` (+${add ?? 0} -${rem ?? 0})`
              : ''
          }`.trim(),
        );
      }
      lines.push('Pick up where this left off.');
      setInput(lines.join('\n'));
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
      await copilotAskStream(
        prompt,
        accessToken,
        { provider: selectedBrain },
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
  }, [input, busy, accessToken, selectedBrain, selectedSessionId, sessions]);

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
          {workspaces.length === 0 && <div className='px-3 py-6 text-center text-xs text-zinc-600'>No workspaces detected. Make sure Founder Node v0.7.2+ is running and Cursor is open.</div>}
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
              {sessionBuckets.today.length > 0 && (
                <div className='mb-1'>
                  <div className='px-3 py-1 text-[0.65rem] font-medium uppercase tracking-wider text-emerald-400/80'>Today</div>
                  {sessionBuckets.today.map((s) => (
                    <SessionRow key={'t-' + s.id} session={s} selected={selectedSessionId === s.id} onSelect={handleSelectSession} dotClass='bg-emerald-400' />
                  ))}
                </div>
              )}
              {sessionBuckets.last7.length > 0 && (
                <div className='mb-1'>
                  <div className='px-3 py-1 text-[0.65rem] font-medium uppercase tracking-wider text-violet-400/80'>Last 7 Days</div>
                  {sessionBuckets.last7.map((s) => (
                    <SessionRow key={'l7-' + s.id} session={s} selected={selectedSessionId === s.id} onSelect={handleSelectSession} dotClass='bg-violet-400' />
                  ))}
                </div>
              )}
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
                  {BRAINS.map((b) => (<option key={b.key} value={b.key}>{b.label} - {b.hint}</option>))}
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
          </div>
        </div>

        {showConnectWizard && (
          <div className='flex flex-col items-center justify-center gap-4 px-6 py-12 text-center'>
            <div className='max-w-md'>
              <h2 className='text-lg font-semibold text-zinc-100'>Connect your IDE</h2>
              <p className='mt-2 text-sm text-zinc-400'>To see your Cursor workspaces here, install Founder Node v0.7.2+ on your laptop. It automatically detects your Cursor sessions and streams them here.</p>
              <ol className='mt-4 space-y-2 text-left text-sm text-zinc-300'>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'><span className='font-semibold text-emerald-400'>1.</span> Download Founder Node v0.7.2 from GitHub Releases</li>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'><span className='font-semibold text-emerald-400'>2.</span> Sign in with the same account</li>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-2.5'><span className='font-semibold text-emerald-400'>3.</span> Open Cursor - workspaces appear here automatically</li>
              </ol>
              <p className='mt-3 text-xs text-zinc-500'>Meanwhile, chat below works with any Brain - no Cursor key needed.</p>
            </div>
          </div>
        )}

        <div ref={scrollRef} className='min-h-0 flex-1 overflow-y-auto px-4 py-4'>
          {selectedWs && !selectedSession && <div className='mb-3 rounded-lg border border-white/5 bg-white/[0.03] px-3 py-2 text-xs text-zinc-400'>Working in: <span className='text-zinc-200'>{selectedWs.label}</span>{selectedWs.branch && <span className='text-zinc-500'> - {selectedWs.branch}</span>}</div>}
          {selectedSession && (
            <div className='mb-3 rounded-lg border border-violet-400/20 bg-violet-500/[0.06] px-3 py-2.5 text-xs text-zinc-300'>
              <div className='flex items-center gap-2'>
                <span className={'h-1.5 w-1.5 shrink-0 rounded-full ' + (Date.now() - new Date(selectedSession.lastActiveAt).getTime() < 86400000 ? 'bg-emerald-400' : 'bg-violet-400')} />
                <span className='font-medium text-zinc-100'>{selectedSession.title}</span>
                {selectedSession.isAgentProject && <span className='rounded bg-violet-500/20 px-1.5 py-0.5 text-[0.6rem] uppercase tracking-wider text-violet-300'>Agent</span>}
                <span className='ml-auto text-zinc-500'>{timeAgo(selectedSession.lastActiveAt)}</span>
              </div>
              {selectedSession.subtitle && <div className='mt-1 pl-3.5 text-zinc-400'>{selectedSession.subtitle}</div>}
              {(selectedSession.repository || selectedSession.branch) && (
                <div className='mt-1 flex items-center gap-2 pl-3.5 text-zinc-500'>
                  {selectedSession.repository && <span className='rounded bg-white/5 px-1.5 py-0.5 text-[0.65rem] text-zinc-300'>{selectedSession.repository}</span>}
                  {selectedSession.branch && <span className='rounded bg-white/5 px-1.5 py-0.5 text-[0.65rem] text-zinc-300'>{selectedSession.branch}</span>}
                  {(typeof selectedSession.filesChangedCount === 'number' || typeof selectedSession.totalLinesAdded === 'number') && (
                    <span className='text-[0.65rem] text-emerald-400/80'>
                      {typeof selectedSession.filesChangedCount === 'number' ? `${selectedSession.filesChangedCount} file(s)` : ''}
                      {typeof selectedSession.totalLinesAdded === 'number' || typeof selectedSession.totalLinesRemoved === 'number' ? ` +${selectedSession.totalLinesAdded ?? 0} -${selectedSession.totalLinesRemoved ?? 0}` : ''}
                    </span>
                  )}
                </div>
              )}
              <div className='mt-1.5 pl-3.5 text-zinc-500'>Resume this Cursor session — your prompt below is pre-filled with its context.</div>
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
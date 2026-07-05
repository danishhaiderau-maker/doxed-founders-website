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
  fetchFounderNodeStatus,
  type FounderNodeStatusRow,
  isSessionExpiredError,
  SESSION_EXPIRED_MESSAGE,
  dispatchToIdeSession,
  fetchIdeDispatchStatus,
  type BridgeSession,
  type ConnectedWorkspace,
  type DesktopBridgeResponse,
  type RecentAgent,
} from '@/lib/api';

type PendingAttachment = {
  name: string;
  previewUrl?: string;
  category?: string;
  /** data: URL for images — Founder Node can paste into Cursor clipboard */
  dataUrl?: string;
};

type ChatMsg = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  provider?: string;
  pending?: boolean;
  thinking?: boolean;
  missingConnections?: CopilotMissingConnection[];
  attachments?: PendingAttachment[];
};

type BrainOption = { key: string; label: string; hint: string };

import { CollapsibleInfo } from '@/components/ui/collapsible-info';
import { FOUNDER_NODE_GITHUB_RELEASES } from '@/components/founder-node-downloads';
import { FOUNDER_NODE_MIN_VERSION, FOUNDER_NODE_MIN_VERSION_LABEL } from '@/lib/founder-node-requirements';
import { cleanTranscriptText, useVoiceInput } from '@/hooks/use-voice-input';
import { VoiceWaveform } from '@/components/voice-waveform';

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
const SCROLL_BOTTOM_THRESHOLD_PX = 96;
const DISPATCH_PAYLOAD_MAX_CHARS = 9_000_000;

function isNearScrollBottom(el: HTMLElement, threshold = SCROLL_BOTTOM_THRESHOLD_PX): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

/** Downscale phone camera photos so dispatch JSON stays under the API 10mb limit. */
async function compressImageForDispatch(
  file: File,
  maxDim = 1600,
  quality = 0.82,
): Promise<string> {
  if (!file.type.startsWith('image/')) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(new Error('read failed'));
      reader.readAsDataURL(file);
    });
  }

  const objectUrl = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('decode failed'));
      el.src = objectUrl;
    });

    let { width, height } = img;
    const scale = Math.min(1, maxDim / Math.max(width, height));
    width = Math.max(1, Math.round(width * scale));
    height = Math.max(1, Math.round(height * scale));

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas unavailable');
    ctx.drawImage(img, 0, 0, width, height);

    const mime = 'image/jpeg';
    const dataUrl = canvas.toDataURL(mime, quality);
    if (dataUrl.length > 4_000_000 && quality > 0.55) {
      return compressImageForDispatch(file, maxDim, quality - 0.12);
    }
    return dataUrl;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/** Never replace a populated thread with an empty API/poll payload. */
function mergeBridgeHistory(
  prev: BridgeMessage[] | null,
  incoming: BridgeMessage[] | null | undefined,
): BridgeMessage[] | null {
  if (!incoming || incoming.length === 0) {
    return prev && prev.length > 0 ? prev : incoming ?? null;
  }
  return incoming;
}

function AgentTypingBubble({ prominent }: { prominent?: boolean }) {
  return (
    <div className={'flex justify-start ' + (prominent ? 'sticky bottom-0 z-10 pb-1' : '')}>
      <div
        className={
          'rounded-2xl text-sm italic text-zinc-300 ' +
          (prominent
            ? 'border border-violet-400/30 bg-violet-500/15 px-4 py-3 shadow-lg shadow-violet-950/40 animate-pulse'
            : 'bg-white/5 px-4 py-2.5')
        }
      >
        <span className='font-medium not-italic text-violet-200/90'>Agent is typing</span>
        <span className='ml-1 inline-flex gap-0.5 align-middle'>
          <span className='inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-violet-300/90 [animation-delay:0ms]' />
          <span className='inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-violet-300/90 [animation-delay:150ms]' />
          <span className='inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-violet-300/90 [animation-delay:300ms]' />
        </span>
      </div>
    </div>
  );
}

function HistoryMessage({ msg }: { msg: BridgeMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isUser = msg.role === 'user';
  const text = msg.content || '';
  const isTyping = msg.partial && !text.trim();
  const overLimit = text.length > HISTORY_MSG_COLLAPSED_LEN;
  const shown = isTyping
    ? 'Agent is typing…'
    : expanded || !overLimit
      ? text
      : text.slice(0, HISTORY_MSG_COLLAPSED_LEN) + '…';
  const timeLabel = msg.timestamp ? timeAgo(msg.timestamp) : null;
  return (
    <div className={'flex ' + (isUser ? 'justify-end' : 'justify-start')}>
      <div className={'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ' + (isUser ? 'bg-violet-500/15 text-violet-50' : 'bg-white/5 text-zinc-100')}>
        <div className='mb-1 flex items-center gap-2 text-[0.65rem] uppercase tracking-wider text-zinc-500'>
          <span>{isUser ? 'You' : msg.role === 'system' ? 'System' : 'Assistant'}</span>
          {msg.model && <span className='text-zinc-600'>· {msg.model}</span>}
          {timeLabel && <span className='text-zinc-600'>· {timeLabel}</span>}
          {msg.streaming && <span className='text-emerald-400/80'>· live</span>}
        </div>
        <p className={'whitespace-pre-wrap break-words ' + (isTyping ? 'italic text-zinc-400' : '')}>
          {shown}
          {msg.streaming && !isTyping && <span className='ml-0.5 inline-block animate-pulse text-zinc-500'>▋</span>}
        </p>
        {overLimit && !isTyping && (
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
  agentTyping,
}: {
  messages: BridgeMessage[] | null;
  loading: boolean;
  error: string | null;
  agentTyping?: boolean;
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
      {agentTyping && !messages.some((m) => m.streaming || m.partial) && (
        <div className='hidden md:block'>
          <AgentTypingBubble prominent />
        </div>
      )}
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
  const [pinnedSession, setPinnedSession] = useState<BridgeSession | null>(null);
  const [history, setHistory] = useState<BridgeMessage[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [nodeStatus, setNodeStatus] = useState({ desktopOnline: false, cursorConnected: false, founderNodeOnline: false });
  const [pairedNodes, setPairedNodes] = useState<FounderNodeStatusRow[]>([]);
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
  const [authStale, setAuthStale] = useState(false);
  const [dispatchNotice, setDispatchNotice] = useState<string | null>(null);
  const [optimisticAgentTyping, setOptimisticAgentTyping] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const historyPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dispatchPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const postDispatchUntilRef = useRef(0);
  const agentTypingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);
  const wasAtBottomRef = useRef(true);
  const forceScrollRef = useRef(false);
  const prevScrollHeightRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** Synchronous guard — React `busy` state updates too late for double-clicks. */
  const sendingRef = useRef(false);
  const lastSendRef = useRef<{ text: string; sessionId: string | null; at: number } | null>(null);
  const stopHistoryPoll = useCallback(() => {
    if (historyPollRef.current) {
      clearTimeout(historyPollRef.current);
      historyPollRef.current = null;
    }
  }, []);

  const stopDispatchPoll = useCallback(() => {
    if (dispatchPollRef.current) {
      clearTimeout(dispatchPollRef.current);
      dispatchPollRef.current = null;
    }
  }, []);

  // Hook owns interim vs final segments — replace the textarea, never append.
  const onVoiceTranscript = useCallback((text: string) => {
    const cleaned = cleanTranscriptText(text);
    if (!cleaned) return;
    setInput(cleaned);
  }, []);

  const {
    phase,
    audioLevel,
    supported: voiceSupported,
    listening,
    starting,
    waitingNetwork,
    voiceError,
    clearVoiceError,
    resetTranscript,
    toggle: toggleVoice,
    stop: stopVoice,
  } = useVoiceInput(onVoiceTranscript);

  useEffect(() => {
    if (voiceError) setError(voiceError);
  }, [voiceError]);

  const pollSessionHistory = useCallback(
    (sessionId: string, opts?: { aggressiveMs?: number }) => {
      if (!accessToken) return;
      stopHistoryPoll();
      if (opts?.aggressiveMs && opts.aggressiveMs > 0) {
        postDispatchUntilRef.current = Date.now() + opts.aggressiveMs;
      }
      const tick = () => {
        void fetchIdeBridgeSessionMessages(accessToken, sessionId)
          .then((msgs) => {
            if (Array.isArray(msgs)) {
              setHistory((prev) => mergeBridgeHistory(prev, msgs));
            }
          })
          .catch(() => undefined);
        void fetchIdeBridgeSessions(accessToken)
          .then((ss) => {
            if (!Array.isArray(ss)) return;
            setSessions(ss);
            const match = ss.find((s) => s.id === sessionId);
            if (match?.messages?.length) {
              setHistory((prev) => mergeBridgeHistory(prev, match.messages));
              setPinnedSession((pin) =>
                pin?.id === sessionId ? { ...pin, ...match, messages: match.messages } : pin,
              );
            }
          })
          .catch(() => undefined);
      };
      tick();
      // 2s while agent is typing / post-dispatch; otherwise 3s.
      const intervalMs = () =>
        agentTypingRef.current || Date.now() < postDispatchUntilRef.current
          ? 2000
          : 3000;
      const schedule = () => {
        historyPollRef.current = setTimeout(() => {
          tick();
          schedule();
        }, intervalMs());
      };
      schedule();
    },
    [accessToken, stopHistoryPoll],
  );

  useEffect(() => () => {
    stopHistoryPoll();
    stopDispatchPoll();
  }, [stopHistoryPoll, stopDispatchPoll]);

  const handleScrollAreaScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = isNearScrollBottom(el);
    wasAtBottomRef.current = atBottom;
    userScrolledUpRef.current = !atBottom;
    prevScrollHeightRef.current = el.scrollHeight;
  }, []);

  /** Auto-scroll only when user was at bottom or explicitly sent / switched session. */
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (!el) return;

      const newHeight = el.scrollHeight;
      const prevHeight = prevScrollHeightRef.current;
      const heightDelta = newHeight - prevHeight;

      if (forceScrollRef.current) {
        forceScrollRef.current = false;
        wasAtBottomRef.current = true;
        userScrolledUpRef.current = false;
        el.scrollTo({ top: newHeight, behavior: 'smooth' });
        prevScrollHeightRef.current = newHeight;
        return;
      }

      if (wasAtBottomRef.current && (heightDelta !== 0 || isNearScrollBottom(el))) {
        el.scrollTop = newHeight;
        wasAtBottomRef.current = true;
        userScrolledUpRef.current = false;
      }

      prevScrollHeightRef.current = newHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [messages, history]);

  const firedInitial = useRef(false);

  const refresh = useCallback(async () => {
    if (!accessToken) return;
    try {
      const catchAuth = <T,>(fallback: T) => (err: unknown): T => {
        if (isSessionExpiredError(err)) setAuthStale(true);
        return fallback;
      };
      const [b, iw, c, a, ss, nodeStatusResp] = await Promise.all([
        fetchDesktopBridge(accessToken).catch(catchAuth(null)),
        fetchIdeBridgeWorkspaces(accessToken).catch(catchAuth([] as IdeWorkspace[])),
        fetchConnectedWorkspaces(accessToken).catch(catchAuth([] as ConnectedWorkspace[])),
        fetchRecentAgents(accessToken).catch(catchAuth(null)),
        fetchIdeBridgeSessions(accessToken).catch(catchAuth([] as BridgeSession[])),
        fetchFounderNodeStatus(accessToken).catch(catchAuth({ nodes: [] as FounderNodeStatusRow[] })),
      ]);
      if (b) { setBridge(b); setLastSync(b.latest?.updatedAt || 'never'); }
      if (iw) setIdeWorkspaces(iw);
      if (c) setConnected(c);
      if (a) { setAgents(a.agents); setNodeStatus({ desktopOnline: a.desktopOnline, cursorConnected: a.cursorConnected, founderNodeOnline: a.founderNodeOnline }); }
      if (ss) setSessions(ss);
      setPairedNodes(nodeStatusResp?.nodes ?? []);
      if (nodeStatusResp?.nodes?.length || b || iw.length || ss.length || a) {
        setAuthStale(false);
        setError(null);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load workspace state';
      if (isSessionExpiredError(e)) {
        setAuthStale(true);
        setError(SESSION_EXPIRED_MESSAGE);
      } else {
        setError(msg);
      }
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
    if (!selectedSessionId || !accessToken) {
      stopHistoryPoll();
      return;
    }
    pollSessionHistory(selectedSessionId);
    return () => stopHistoryPoll();
  }, [selectedSessionId, accessToken, pollSessionHistory, stopHistoryPoll]);

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

  const accountHasPairedNode = pairedNodes.length > 0;
  const accountNodeOnline = pairedNodes.some((n) => n.status === 'online');
  const founderNodeHeartbeating =
    nodeStatus.founderNodeOnline || nodeStatus.desktopOnline || accountNodeOnline;
  const isNodeLive =
    founderNodeHeartbeating ||
    !!bridge?.latest ||
    !!bridge?.nodes?.length ||
    ideWorkspaces.length > 0 ||
    sessions.length > 0;
  const cursorConnected =
    nodeStatus.cursorConnected ||
    sessions.length > 0 ||
    ideWorkspaces.some((w) => w.ideProvider === 'cursor') ||
    Boolean(bridge?.latest || bridge?.nodes?.length);
  const pairingMismatchHint =
    authStale
      ? SESSION_EXPIRED_MESSAGE
      : !isNodeLive && !accountHasPairedNode
      ? 'No Founder Node is paired to this account. In Founder Node tray → Repair connection, generate a code in Settings → Builder, and paste it in the tray app (not the browser).'
      : !isNodeLive && accountHasPairedNode && !accountNodeOnline
        ? 'Your account has a paired node but it is not heartbeating. Open Founder Node from the tray, click Sync now, or re-pair with a fresh code from Settings → Builder.'
        : null;
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

  const selectedSession = useMemo(() => {
    if (!selectedSessionId) return null;
    return sessions.find((s) => s.id === selectedSessionId) ?? pinnedSession;
  }, [sessions, selectedSessionId, pinnedSession]);
  const selectedAgentTyping =
    optimisticAgentTyping ||
    selectedSession?.agentTyping === true ||
    Boolean(history?.some((m) => m.streaming || m.partial));

  useEffect(() => {
    agentTypingRef.current = selectedAgentTyping;
  }, [selectedAgentTyping]);

  useEffect(() => {
    if (!optimisticAgentTyping) return;
    if (history?.some((m) => m.streaming || m.partial)) {
      setOptimisticAgentTyping(false);
    }
  }, [history, optimisticAgentTyping]);

  const handleAttachFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    const next: PendingAttachment[] = [];
    for (const file of Array.from(files)) {
      let dataUrl: string | undefined;
      let previewUrl: string | undefined;
      if (file.type.startsWith('image/')) {
        try {
          dataUrl = await compressImageForDispatch(file);
          previewUrl = dataUrl;
        } catch {
          try {
            dataUrl = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result || ''));
              reader.onerror = () => reject(new Error('read failed'));
              reader.readAsDataURL(file);
            });
            previewUrl = dataUrl;
          } catch {
            previewUrl = URL.createObjectURL(file);
          }
        }
      }
      try {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/founder-vault/upload', { method: 'POST', body: form });
        if (!res.ok) {
          next.push({ name: file.name, previewUrl, dataUrl, category: file.type.startsWith('image/') ? 'screenshot' : 'document' });
          continue;
        }
        const body = (await res.json()) as {
          items?: Array<{ name: string; category: string }>;
        };
        const saved = body.items?.[0];
        next.push({
          name: saved?.name ?? file.name,
          category: saved?.category,
          previewUrl,
          dataUrl,
        });
      } catch {
        next.push({ name: file.name, previewUrl, dataUrl });
      }
    }
    if (next.length) setPendingAttachments((prev) => [...prev, ...next]);
  }, []);

  // Load the conversation thread whenever the user picks a session. We show
  // whatever was already embedded in the session list immediately (no network
  // wait), then refresh from the dedicated endpoint so the user sees the
  // latest messages even if the heartbeat payload was truncated.
  useEffect(() => {
    if (!selectedSessionId || !accessToken) {
      setHistory(null);
      setHistoryError(null);
      setPinnedSession(null);
      return;
    }
    let cancelled = false;
    const embedded =
      selectedSession?.messages ??
      pinnedSession?.messages ??
      null;

    setHistory((prev) => mergeBridgeHistory(prev, embedded));
    setHistoryError(null);
    setHistoryLoading(!embedded?.length);

    fetchIdeBridgeSessionMessages(accessToken, selectedSessionId)
      .then((msgs) => {
        if (cancelled) return;
        if (Array.isArray(msgs)) {
          setHistory((prev) => mergeBridgeHistory(prev, msgs));
        }
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
  }, [selectedSessionId, accessToken, pinnedSession?.id]);

  const handleSelectSession = useCallback((session: BridgeSession) => {
    setSelectedSessionId(session.id);
    setPinnedSession(session);
    setSidebarOpen(false);
    forceScrollRef.current = true;
    userScrolledUpRef.current = false;
    if (session.messages?.length) {
      setHistory(session.messages);
      setHistoryLoading(false);
      setHistoryError(null);
    }
    // Don't prefill the input with a technical summary — the conversation
    // thread above shows the full history; the user just continues typing
    // as if replying in a chat app.
    setInput('');
  }, []);

  const pollDispatchDelivery = useCallback(
    (
      dispatchId: string,
      nodeOnline: boolean,
      onDone: (outcome: 'delivered' | 'queued' | 'failed', detail?: string) => void,
    ) => {
      stopDispatchPoll();
      const started = Date.now();
      const maxMs = nodeOnline ? 90_000 : 8_000;
      const tick = async () => {
        try {
          const status = await fetchIdeDispatchStatus(accessToken, dispatchId);
          if (status.delivered) {
            onDone('delivered');
            return;
          }
          if (status.failed) {
            const detail = status.result?.replace(/^error:\s*/i, '') ?? 'Delivery failed on your PC';
            onDone('failed', detail);
            return;
          }
          if (status.status === 'DISPATCHED' && !status.delivered) {
            onDone(
              'failed',
              status.result?.replace(/^error:\s*/i, '') ?? 'Cursor delivery did not complete on your PC',
            );
            return;
          }
          if (status.status === 'DISPATCHING') {
            setDispatchNotice('Delivering to Cursor on your PC…');
          } else if (status.status === 'PENDING' && nodeOnline) {
            setDispatchNotice('Delivering to Cursor on your PC…');
          } else if (status.status === 'PENDING' && !nodeOnline) {
            onDone('queued');
            return;
          }
          if (Date.now() - started >= maxMs) {
            onDone(nodeOnline ? 'failed' : 'queued', nodeOnline ? 'Timed out waiting for Founder Node' : undefined);
            return;
          }
          dispatchPollRef.current = setTimeout(() => {
            void tick();
          }, 2000);
        } catch {
          if (Date.now() - started >= maxMs) {
            onDone('queued');
            return;
          }
          dispatchPollRef.current = setTimeout(() => {
            void tick();
          }, 2000);
        }
      };
      void tick();
    },
    [accessToken, stopDispatchPoll],
  );

  const handleSend = useCallback(async () => {
    const prompt = cleanTranscriptText(input.trim());
    if ((!prompt && pendingAttachments.length === 0) || busy) return;
    // Sync guard — `busy` flips too late for double-clicks / Enter + Send.
    if (sendingRef.current) return;
    const now = Date.now();
    const last = lastSendRef.current;
    if (
      last &&
      last.text === prompt &&
      last.sessionId === selectedSessionId &&
      now - last.at < 60_000
    ) {
      setDispatchNotice('Duplicate blocked — wait a minute or edit your message.');
      setTimeout(() => setDispatchNotice(null), 5000);
      return;
    }

    const sessionForDispatch = selectedSessionId
      ? sessions.find((s) => s.id === selectedSessionId)
      : null;
    const dispatchToSelectedSession = Boolean(accessToken && sessionForDispatch);

    if (dispatchToSelectedSession && authStale) {
      setError(SESSION_EXPIRED_MESSAGE);
      setDispatchNotice('Sign in again to dispatch to Cursor.');
      setTimeout(() => setDispatchNotice(null), 8000);
      return;
    }

    sendingRef.current = true;
    lastSendRef.current = { text: prompt, sessionId: selectedSessionId, at: now };

    if (phase !== 'idle') stopVoice();
    resetTranscript();
    setInput('');
    const attachmentsForSend = pendingAttachments;
    setPendingAttachments([]);

    const attachNote =
      attachmentsForSend.length > 0
        ? '\n\n[Attachments: ' + attachmentsForSend.map((a) => a.name).join(', ') + ']'
        : '';
    // Embed image data URLs for Founder Node clipboard paste (stripped before Cursor sees text).
    const imageBlocks = attachmentsForSend
      .filter((a) => a.dataUrl && a.dataUrl.startsWith('data:image/'))
      .map(
        (a) =>
          `\n<!--founder-attach:image:${a.name}\n${a.dataUrl}\n-->`,
      )
      .join('');
    const fullPrompt = (prompt + attachNote + imageBlocks).trim();
    const userMsg: ChatMsg = {
      id: 'u-' + Date.now(),
      role: 'user',
      text: prompt || '(attachment)',
      attachments: attachmentsForSend.length
        ? attachmentsForSend.map(({ name, previewUrl, category }) => ({
            name,
            previewUrl,
            category,
          }))
        : undefined,
    };
    setBusy(true);
    setError(null);

    // Cursor session selected → remote control only; no platform Brain / RULE_BASED fallback.
    if (dispatchToSelectedSession && sessionForDispatch) {
      if (fullPrompt.length > DISPATCH_PAYLOAD_MAX_CHARS) {
        const msg =
          'Message too large (usually a photo). Try a smaller image or fewer attachments.';
        setDispatchNotice(`Cursor dispatch failed: ${msg}`);
        setError(msg);
        setBusy(false);
        sendingRef.current = false;
        setTimeout(() => setDispatchNotice(null), 8000);
        return;
      }

      forceScrollRef.current = true;
      setOptimisticAgentTyping(false);
      setDispatchNotice(
        founderNodeHeartbeating
          ? 'Sending to server…'
          : accountHasPairedNode
            ? 'Queuing — Founder Node offline on your PC…'
            : 'Queuing — pair Founder Node on your PC to deliver…',
      );
      setHistory((prev) => [
        ...(prev ?? []),
        {
          role: 'user',
          content: prompt || '(attachment)',
          timestamp: new Date().toISOString(),
        },
      ]);
      const ideProvider = sessionForDispatch.ideProvider || 'cursor';
      try {
        const created = await dispatchToIdeSession(
          accessToken,
          sessionForDispatch.id,
          fullPrompt,
          ideProvider,
        );
        pollSessionHistory(sessionForDispatch.id, { aggressiveMs: 45_000 });

        if (!founderNodeHeartbeating) {
          if (accountHasPairedNode) {
            setDispatchNotice(
              'Queued on server — Founder Node offline on your PC. Open Founder Node on your desktop to deliver to Cursor.',
            );
          } else {
            setDispatchNotice(
              'Queued on server — install and pair Founder Node on your PC to deliver to Cursor.',
            );
          }
          setTimeout(() => setDispatchNotice(null), 20_000);
        } else {
          setDispatchNotice('Delivering to Cursor on your PC…');
          pollDispatchDelivery(created.id, true, (outcome, detail) => {
            if (outcome === 'delivered') {
              setOptimisticAgentTyping(true);
              setDispatchNotice('Delivered to Cursor — agent is working…');
              setTimeout(() => setOptimisticAgentTyping(false), 45_000);
              setTimeout(() => setDispatchNotice(null), 12_000);
            } else if (outcome === 'queued') {
              setDispatchNotice(
                'Queued on server — waiting for Founder Node on your PC.',
              );
              setTimeout(() => setDispatchNotice(null), 20_000);
            } else {
              setDispatchNotice(
                `Cursor delivery failed on your PC${detail ? `: ${detail.slice(0, 160)}` : ''}. Check Founder Node tray and that Cursor is open.`,
              );
              setError(detail ?? 'Cursor delivery failed on your PC');
              setTimeout(() => setDispatchNotice(null), 16_000);
            }
          });
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Failed to dispatch to Cursor';
        setDispatchNotice(`Cursor dispatch failed: ${msg}`);
        setError(msg);
        setTimeout(() => setDispatchNotice(null), 8000);
      } finally {
        setBusy(false);
        sendingRef.current = false;
      }
      return;
    }

    forceScrollRef.current = true;
    const assistantId = 'a-' + Date.now();
    const assistantMsg: ChatMsg = { id: assistantId, role: 'assistant', text: '', pending: true, thinking: true };
    setMessages((m) => [...m, userMsg, assistantMsg]);

    let firstTokenSeen = false;
    let providerLabel = selectedBrain;

    const patch = (mut: (msg: ChatMsg) => ChatMsg) =>
      setMessages((m) => m.map((msg) => (msg.id === assistantId ? mut(msg) : msg)));

    try {
      const effectiveProvider = selectedBrain === 'BYOK' ? 'BYOK' : selectedBrain;
      await copilotAskStream(
        fullPrompt,
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
      sendingRef.current = false;
    }
  }, [input, busy, accessToken, selectedBrain, selectedSessionId, sessions, byokKey, pollSessionHistory, pollDispatchDelivery, pendingAttachments, phase, stopVoice, resetTranscript, authStale, founderNodeHeartbeating, accountHasPairedNode]);

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
          {workspaces.length === 0 && (
            <div className='px-3 py-6 text-center text-xs text-zinc-600'>
              {pairingMismatchHint ??
                `No workspaces detected. Make sure Founder Node ${FOUNDER_NODE_MIN_VERSION_LABEL} is running, Cursor is open, and tray shows Last sync: just now.`}
            </div>
          )}
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
              <div className='flex items-center gap-2'><span className={'h-2 w-2 rounded-full ' + (founderNodeHeartbeating ? 'bg-emerald-400' : 'bg-zinc-600')} /><span className='text-zinc-500'>Founder Node</span><span className='ml-auto text-zinc-200'>{founderNodeHeartbeating ? 'Online' : accountHasPairedNode ? 'Offline' : 'Not paired'}</span></div>
              <div className='flex items-center gap-2'><span className={'h-2 w-2 rounded-full ' + (cursorConnected ? 'bg-emerald-400' : 'bg-zinc-600')} /><span className='text-zinc-500'>Cursor</span><span className='ml-auto text-zinc-200'>{cursorConnected ? 'Connected' : 'Not connected'}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Repository</span><span className='ml-auto truncate text-zinc-200'>{repoName}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Branch</span><span className='ml-auto text-zinc-200'>{branchName}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>AI</span><span className='ml-auto text-emerald-400'>{brainLabel}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Active Agents</span><span className='ml-auto text-zinc-200'>{agentCount}</span></div>
              <div className='flex items-center gap-2'><span className='text-zinc-500'>Last Sync</span><span className='ml-auto text-zinc-400'>{timeAgo(lastSync)}</span></div>
            </div>
            {authStale && (
              <div className='mt-3 rounded-lg border border-rose-400/25 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-200/90'>
                {SESSION_EXPIRED_MESSAGE} Founder Node may still show connected in the tray — that uses a separate device token.
              </div>
            )}
            {selectedSession && !founderNodeHeartbeating && (
              <div className='mt-3 rounded-lg border border-amber-400/25 bg-amber-500/[0.08] px-3 py-2 text-xs text-amber-200/90'>
                {accountHasPairedNode
                  ? 'Founder Node is offline on your PC. Messages queue on the server — open Founder Node on your desktop to deliver them to Cursor.'
                  : 'Pair Founder Node on your PC to relay phone messages into Cursor. Until then, dispatches stay queued on the server.'}
              </div>
            )}
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
              <p className='mt-2 text-sm text-zinc-400'>To see your Cursor workspaces here, install Founder Node {FOUNDER_NODE_MIN_VERSION_LABEL} on your laptop. It automatically detects your Cursor sessions and streams them here.</p>
              {pairingMismatchHint && (
                <p className='mt-3 rounded-lg border border-amber-400/20 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200/90'>
                  {pairingMismatchHint}
                </p>
              )}
              <div className='mt-4 space-y-2'>
                <a
                  href={FOUNDER_DEN_ONBOARD_URL}
                  className='inline-flex w-full items-center justify-center rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500'
                >
                  Download Founder Node v{FOUNDER_NODE_MIN_VERSION} — setup hub
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

        <div
          ref={scrollRef}
          onScroll={handleScrollAreaScroll}
          className='min-h-0 flex-1 overflow-y-auto overscroll-y-contain px-4 py-4 [overflow-anchor:auto] [-webkit-overflow-scrolling:touch]'
          style={{ touchAction: 'pan-y', overflowAnchor: 'auto' }}
        >
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
            <HistoryThread
              messages={history}
              loading={historyLoading}
              error={historyError}
              agentTyping={selectedAgentTyping}
            />
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
                  {m.attachments && m.attachments.length > 0 && (
                    <div className='mt-2 flex flex-wrap gap-2'>
                      {m.attachments.map((a) =>
                        a.previewUrl ? (
                          <img
                            key={a.name}
                            src={a.previewUrl}
                            alt={a.name}
                            className='max-h-32 rounded-lg border border-white/10'
                          />
                        ) : (
                          <span
                            key={a.name}
                            className='rounded-md bg-white/5 px-2 py-1 text-[0.65rem] text-zinc-400'
                          >
                            📎 {a.name}
                          </span>
                        ),
                      )}
                    </div>
                  )}
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
            <div
              className={
                'mb-2 text-xs ' +
                (dispatchNotice.includes('failed') ||
                dispatchNotice.includes('blocked') ||
                dispatchNotice.includes('delivery failed')
                  ? 'text-rose-400'
                  : dispatchNotice.includes('Queued') ||
                      dispatchNotice.includes('offline') ||
                      dispatchNotice.includes('pair') ||
                      dispatchNotice.includes('Delivering') ||
                      dispatchNotice.includes('Waiting')
                    ? 'text-amber-300/95'
                    : dispatchNotice.includes('Delivered')
                      ? 'text-emerald-400/90'
                      : 'text-zinc-400')
              }
            >
              → {dispatchNotice}
            </div>
          )}
          {selectedSession &&
            selectedAgentTyping &&
            !history?.some((m) => m.streaming || m.partial) && (
            <div className='mx-auto mb-2 max-w-3xl md:hidden'>
              <AgentTypingBubble prominent />
            </div>
          )}
          {pendingAttachments.length > 0 && (
            <div className='mx-auto mb-2 flex max-w-3xl flex-wrap gap-2'>
              {pendingAttachments.map((a) => (
                <span
                  key={a.name}
                  className='inline-flex items-center gap-1 rounded-md bg-white/5 px-2 py-1 text-[0.65rem] text-zinc-300'
                >
                  📎 {a.name}
                  <button
                    type='button'
                    onClick={() =>
                      setPendingAttachments((prev) => prev.filter((x) => x.name !== a.name))
                    }
                    className='text-zinc-500 hover:text-zinc-200'
                    aria-label={'Remove ' + a.name}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className='mx-auto max-w-3xl'>
            <div className='flex items-end gap-2'>
              <input
                ref={fileInputRef}
                type='file'
                accept='image/*,.pdf,.txt,.log,.csv,.zip,.md,.json'
                multiple
                className='hidden'
                onChange={(e) => {
                  void handleAttachFiles(e.target.files);
                  e.target.value = '';
                }}
              />
              <div className='flex shrink-0 flex-col gap-1'>
                <button
                  type='button'
                  onClick={() => {
                    clearVoiceError();
                    setError(null);
                    if (!voiceSupported) {
                      setError(
                        'Voice needs Chrome or Edge on desktop with microphone permission (HTTPS).',
                      );
                      return;
                    }
                    toggleVoice(input);
                  }}
                  className={
                    'flex h-11 w-11 items-center justify-center rounded-xl text-sm transition ' +
                    (listening
                      ? 'bg-red-600 text-white'
                      : waitingNetwork
                        ? 'bg-sky-700/90 text-white'
                        : starting
                          ? 'bg-amber-600/90 text-white'
                          : 'border border-white/10 bg-[#12121a] text-zinc-400 hover:text-zinc-100')
                  }
                  title={listening ? 'Stop recording' : 'Voice input (speech to text)'}
                >
                  {listening ? '⏹' : '🎤'}
                </button>
                <button
                  type='button'
                  onClick={() => fileInputRef.current?.click()}
                  className='flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-[#12121a] text-zinc-400 transition hover:text-zinc-100'
                  title='Attach image or file'
                >
                  📎
                </button>
              </div>
              <textarea
                value={input}
                onChange={(e) => {
                  if (phase !== 'idle') stopVoice();
                  setInput(e.target.value);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                rows={1}
                placeholder={
                  selectedSession
                    ? 'Reply to this Cursor chat — delivered via Founder Node on your PC'
                    : sessions.length > 0
                      ? 'Select a Cursor chat in the sidebar to dispatch to your IDE'
                      : 'Message Founder OS — shift+enter for newline'
                }
                className='min-h-[44px] flex-1 resize-none rounded-xl border border-white/10 bg-[#12121a] px-4 py-3 text-base text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-400/40 sm:text-sm'
              />
              <button
                type='button'
                onClick={handleSend}
                disabled={busy || (!input.trim() && pendingAttachments.length === 0)}
                className='min-h-[48px] min-w-[48px] touch-manipulation rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-white transition hover:bg-emerald-400 disabled:opacity-40 sm:min-h-0 sm:min-w-0'
              >
                {busy ? '...' : 'Send'}
              </button>
            </div>
            {(listening || starting || waitingNetwork) && (
              <div className='mt-1.5 flex items-center gap-2 text-[0.65rem] text-zinc-500'>
                <VoiceWaveform phase={phase} level={audioLevel} />
                <span>
                  {listening
                    ? 'Listening — speak now'
                    : waitingNetwork
                      ? 'Waiting for network…'
                      : 'Starting microphone…'}
                </span>
              </div>
            )}
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
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  copilotAskStream,
  fetchConnectedWorkspaces,
  fetchDesktopBridge,
  fetchIdeBridgeWorkspaces,
  fetchRecentAgents,
  type ConnectedWorkspace,
  type CopilotStreamEvent,
  type DesktopBridgeResponse,
  type RecentAgent,
} from '@/lib/api';

type ChatMsg = { id: string; role: 'user' | 'assistant'; text: string; provider?: string; pending?: boolean };

type BrainOption = { key: string; label: string; hint: string };

const BRAINS: BrainOption[] = [
  { key: 'GLM', label: 'GLM 5.2', hint: 'Promo - fast' },
  { key: 'DEEPSEEK', label: 'DeepSeek V3', hint: 'Platform fallback' },
  { key: 'ANTHROPIC', label: 'Claude', hint: 'Anthropic' },
  { key: 'OPENAI', label: 'GPT', hint: 'OpenAI' },
  { key: 'GEMINI', label: 'Gemini', hint: 'Google' },
  { key: 'OLLAMA', label: 'Ollama', hint: 'Local - Founder Node' },
  { key: 'OPENROUTER', label: 'OpenRouter', hint: 'Multi-model' },
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
  const [nodeStatus, setNodeStatus] = useState({ desktopOnline: false, cursorConnected: false, founderNodeOnline: false });
  const [selectedBrain, setSelectedBrain] = useState<string>('GLM');
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedWsId, setSelectedWsId] = useState<string | null>(null);
  const [fullScreenPanel, setFullScreenPanel] = useState<'none' | 'social' | 'settings'>('none');
  const scrollRef = useRef<HTMLDivElement>(null);
  const firedInitial = useRef(false);

  const refresh = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [b, iw, c, a] = await Promise.all([
        fetchDesktopBridge(accessToken).catch(() => null),
        fetchIdeBridgeWorkspaces(accessToken).catch(() => [] as IdeWorkspace[]),
        fetchConnectedWorkspaces(accessToken).catch(() => [] as ConnectedWorkspace[]),
        fetchRecentAgents(accessToken).catch(() => null),
      ]);
      if (b) setBridge(b);
      if (iw) setIdeWorkspaces(iw);
      if (c) setConnected(c);
      if (a) { setAgents(a.agents); setNodeStatus({ desktopOnline: a.desktopOnline, cursorConnected: a.cursorConnected, founderNodeOnline: a.founderNodeOnline }); }
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

    // 1. Real Cursor workspaces from the IDE bridge (discovered by Founder Node)
    for (const iw of ideWorkspaces) {
      const id = 'cursor:' + iw.id;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({
        id,
        label: iw.title,
        sub: iw.branch ? iw.branch : (iw.repository ? iw.repository.split('/').pop() || iw.repository : iw.ideProvider),
        source: 'cursor',
        ideProvider: iw.ideProvider,
        branch: iw.branch,
        agentStatus: iw.hasActiveAgent ? 'running' : undefined,
        lastActive: iw.lastActiveAt,
      });
    }

    // 2. Desktop bridge snapshots (live node data)
    if (bridge?.nodes?.length) {
      for (const n of bridge.nodes) {
        const id = 'bridge:' + n.nodeId;
        if (seen.has(id)) continue;
        seen.add(id);
        items.push({
          id,
          label: n.taskLabel || n.label || 'Cursor workspace',
          sub: n.branch ? 'branch: ' + n.branch : 'live',
          source: 'bridge',
          branch: n.branch,
          agentStatus: n.agentStatus,
          lastActive: n.updatedAt,
        });
      }
    }

    // 3. Connected workspaces from DB
    for (const c of connected) {
      const id = 'ws:' + c.id;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({
        id,
        label: c.label,
        sub: c.repository || c.ideProvider || 'workspace',
        source: 'connected',
        ideProvider: c.ideProvider,
        branch: c.branch,
        lastActive: c.lastActiveAt,
      });
    }

    return items;
  }, [ideWorkspaces, bridge, connected]);

  const isNodeLive = nodeStatus.founderNodeOnline || !!bridge?.latest || !!bridge?.nodes?.length;
  const cursorConnected = nodeStatus.cursorConnected;
  const agentCount = agents.length;

  const handleSend = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    const userMsg: ChatMsg = { id: 'u-' + Date.now(), role: 'user', text: prompt };
    const assistantId = 'a-' + Date.now();
    const assistantMsg: ChatMsg = { id: assistantId, role: 'assistant', text: '', pending: true };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    setInput('');
    setBusy(true);
    setError(null);
    try {
      const stream = copilotAskStream(accessToken, { prompt, provider: selectedBrain });
      let acc = '';
      let provider = selectedBrain;
      for await (const ev of stream as AsyncGenerator<CopilotStreamEvent>) {
        if (ev.type === 'chunk') {
          acc += ev.text;
          setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, text: acc, pending: false } : msg)));
        } else if (ev.type === 'attribution') {
          provider = ev.provider || provider;
        } else if (ev.type === 'done') {
          acc = ev.answer;
          setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, text: acc, provider: ev.answerProvider, pending: false } : msg)));
        } else if (ev.type === 'error') {
          setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, text: 'Warning: ' + ev.message, pending: false } : msg)));
          setError(ev.message);
        }
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Chat stream failed';
      setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, text: 'Warning: ' + msg, pending: false } : msg)));
      setError(msg);
    } finally {
      setBusy(false);
    }
  }, [input, busy, accessToken, selectedBrain]);

  const selectedWs = workspaces.find((w) => w.id === selectedWsId) ?? null;
  const showConnectWizard = !isNodeLive && workspaces.length === 0;

  return (
    <div className='flex h-[calc(100vh-3.5rem)] w-full bg-[#08080c] text-zinc-100'>
      {/* Left bar - workspaces */}
      <aside className='flex w-72 shrink-0 flex-col border-r border-white/5 bg-[#0a0a0f]'>
        <div className='flex items-center justify-between px-4 py-3'>
          <span className='text-xs font-semibold uppercase tracking-wider text-zinc-400'>Workspaces</span>
          <button onClick={refresh} className='text-xs text-zinc-500 hover:text-zinc-200' aria-label='Refresh'>retry</button>
        </div>
        <div className='min-h-0 flex-1 overflow-y-auto px-2 pb-3'>
          {workspaces.length === 0 && <div className='px-3 py-6 text-center text-xs text-zinc-600'>No workspaces detected. Make sure Founder Node is running and Cursor is open.</div>}
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
        </div>
        <div className='border-t border-white/5 px-2 py-2'>
          <button onClick={() => setFullScreenPanel('social')} className='mb-1 block w-full rounded-md px-3 py-1.5 text-left text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200'>Show social</button>
          <button onClick={() => setFullScreenPanel('settings')} className='block w-full rounded-md px-3 py-1.5 text-left text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200'>Show settings</button>
        </div>
      </aside>

      {/* Main - chat + status */}
      <main className='flex min-w-0 flex-1 flex-col'>
        {/* Status strip */}
        <div className='flex items-center gap-3 border-b border-white/5 bg-[#0a0a0f] px-4 py-2 text-xs'>
          <span className={'h-2 w-2 rounded-full ' + (isNodeLive ? 'bg-emerald-400' : 'bg-zinc-600')} title={isNodeLive ? 'Founder Node live' : 'Founder Node offline'} />
          <span className='text-zinc-300'>{isNodeLive ? 'Founder Node live' : 'Node offline'}</span>
          {cursorConnected && <span className='text-emerald-400/80'>- Cursor detected</span>}
          {bridge?.latest?.branch && <span className='text-zinc-500'>- {bridge.latest.branch}</span>}
          {agentCount > 0 && <span className='text-zinc-500'>- {agentCount} agent{agentCount > 1 ? 's' : ''}</span>}
          <div className='ml-auto flex items-center gap-2'>
            <label className='text-zinc-500'>Brain</label>
            <select value={selectedBrain} onChange={(e) => setSelectedBrain(e.target.value)} className='rounded-md border border-white/10 bg-[#12121a] px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:ring-1 focus:ring-emerald-400/40'>
              {BRAINS.map((b) => (<option key={b.key} value={b.key}>{b.label} - {b.hint}</option>))}
            </select>
          </div>
        </div>

        {/* Connection wizard */}
        {showConnectWizard && (
          <div className='flex flex-col items-center justify-center gap-4 px-6 py-16 text-center'>
            <div className='max-w-md'>
              <h2 className='text-lg font-semibold text-zinc-100'>Welcome to Founder OS</h2>
              <p className='mt-2 text-sm text-zinc-400'>Your remote operating system for desktop development. Connect a Founder Node to your laptop to see your live Cursor workspaces, resume agents, and dispatch work - all from here.</p>
              <ol className='mt-6 space-y-3 text-left text-sm text-zinc-300'>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-3'><span className='font-semibold text-emerald-400'>1.</span> Install Founder Node on your laptop and sign in with the same account.</li>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-3'><span className='font-semibold text-emerald-400'>2.</span> Open Cursor - Founder Node will detect it automatically.</li>
                <li className='rounded-lg border border-white/5 bg-white/5 px-4 py-3'><span className='font-semibold text-emerald-400'>3.</span> Your recent workspaces appear in the left bar. Click one to resume.</li>
              </ol>
              <p className='mt-4 text-xs text-zinc-500'>Meanwhile, the chat below works with any Brain - no Cursor key needed.</p>
            </div>
          </div>
        )}

        {/* Chat messages */}
        <div ref={scrollRef} className='min-h-0 flex-1 overflow-y-auto px-4 py-4'>
          {selectedWs && <div className='mb-3 rounded-lg border border-white/5 bg-white/[0.03] px-3 py-2 text-xs text-zinc-400'>Working in: <span className='text-zinc-200'>{selectedWs.label}</span>{selectedWs.branch && <span className='text-zinc-500'> - {selectedWs.branch}</span>}</div>}
          {messages.length === 0 && !showConnectWizard && <div className='flex h-full items-center justify-center text-sm text-zinc-600'>Ask anything. Founder OS will route your request to the right Brain and dispatch to your IDE.</div>}
          <div className='mx-auto max-w-3xl space-y-3'>
            {messages.map((m) => (
              <div key={m.id} className={'flex ' + (m.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div className={'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ' + (m.role === 'user' ? 'bg-emerald-500/15 text-emerald-50' : 'bg-white/5 text-zinc-100')}>
                  <p className='whitespace-pre-wrap break-words'>{m.text || (m.pending ? '...' : '')}</p>
                  {m.provider && !m.pending && m.role === 'assistant' && <p className='mt-1 text-xs text-zinc-500'>via {m.provider}</p>}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Input */}
        <div className='border-t border-white/5 bg-[#0a0a0f] px-4 py-3'>
          {error && <div className='mb-2 text-xs text-rose-400'>{error}</div>}
          <div className='mx-auto flex max-w-3xl items-end gap-2'>
            <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }} rows={1} placeholder='Message Founder OS - shift+enter for newline' className='min-h-[44px] flex-1 resize-none rounded-xl border border-white/10 bg-[#12121a] px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-400/40' />
            <button onClick={handleSend} disabled={busy || !input.trim()} className='rounded-xl bg-emerald-500 px-4 py-3 text-sm font-semibold text-white transition hover:bg-emerald-400 disabled:opacity-40'>{busy ? '...' : 'Send'}</button>
          </div>
        </div>
      </main>

      {/* Full-screen social overlay */}
      {fullScreenPanel === 'social' && socialPanel && (
        <div className='fixed inset-0 z-50 flex flex-col bg-[#08080c]'>
          <div className='flex items-center justify-between border-b border-white/5 px-4 py-3'>
            <span className='text-sm font-semibold text-zinc-200'>Social</span>
            <button onClick={() => setFullScreenPanel('none')} className='rounded-md bg-white/5 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/10'>Hide social</button>
          </div>
          <div className='min-h-0 flex-1 overflow-y-auto p-4'>{socialPanel}</div>
        </div>
      )}

      {/* Full-screen settings overlay */}
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
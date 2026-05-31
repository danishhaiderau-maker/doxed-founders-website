'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  copilotAsk,
  dispatchCursorCloudBuild,
  fetchBuilderSettings,
  fetchCopilotMemory,
  ProjectMemory,
} from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  provider?: string;
  routedAgent?: string;
  runtimeTools?: string[];
};

const STORAGE_KEY = 'dcf-copilot-chat-v1';

function loadMessages(): ChatMessage[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as ChatMessage[]) : [];
  } catch {
    return [];
  }
}

function saveMessages(messages: ChatMessage[]) {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-40)));
  } catch {
    /* ignore quota */
  }
}

type FounderCopilotChatProps = {
  accessToken: string;
  onResult?: (answer: string) => void;
  variant?: 'default' | 'hero' | 'embedded';
  initialPrompt?: string | null;
  onInitialPromptConsumed?: () => void;
  agentTemplate?: string | null;
};

const ASK_CHIPS = [
  'What is the most pressing issue?',
  'What am I working on right now?',
  'What changed this week?',
  'What should I ship today?',
  'Explain this project to investors.',
];

const QUICK_ACTIONS = [
  { label: 'Continue where I left off', prompt: 'Resume work — what should I finish next?' },
  { label: 'Finish MVP', prompt: 'What is left to finish the MVP?' },
  { label: 'Create tokenomics', prompt: 'Create tokenomics draft for community allocation.' },
  { label: 'Prepare Raise', prompt: 'Prepare launch roadmap for Raise Room.' },
  { label: 'Weekly update', prompt: "Generate this week's update." },
  { label: 'Launch readiness', prompt: 'Create launch readiness report.' },
];

function hasChatLlmProvider(
  providers: { key: string; connectMode?: string; connected: boolean }[],
) {
  return providers.some(
    (p) => p.connectMode === 'api_key' && p.connected && p.key !== 'RULE_BASED',
  );
}

export function FounderCopilotChat({
  accessToken,
  onResult,
  variant = 'default',
  initialPrompt,
  onInitialPromptConsumed,
  agentTemplate,
}: FounderCopilotChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [defaultProvider, setDefaultProvider] = useState('RULE_BASED');
  const [cursorConnected, setCursorConnected] = useState(false);
  const [llmConnected, setLlmConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const consumedPromptRef = useRef<string | null>(null);

  const onTranscript = useCallback((text: string) => {
    setPrompt(text);
  }, []);
  const { listening, supported, toggle, stop } = useVoiceInput(onTranscript);

  useEffect(() => {
    setMessages(loadMessages());
  }, []);

  useEffect(() => {
    saveMessages(messages);
  }, [messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  const loadMeta = useCallback(async () => {
    try {
      const [mem, builder] = await Promise.all([
        fetchCopilotMemory(accessToken),
        fetchBuilderSettings(accessToken),
      ]);
      setMemory(mem);
      setDefaultProvider(builder.defaultProvider);
      setCursorConnected(builder.providers.some((p) => p.key === 'CURSOR' && p.connected));
      setLlmConnected(hasChatLlmProvider(builder.providers));
    } catch {
      setMemory(null);
    }
  }, [accessToken]);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    if (!initialPrompt?.trim() || busy) return;
    if (consumedPromptRef.current === initialPrompt) return;
    consumedPromptRef.current = initialPrompt;
    void sendMessage(initialPrompt);
    onInitialPromptConsumed?.();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot quick action from dashboard
  }, [initialPrompt, busy]);

  async function sendMessage(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    stop();
    setError(null);
    setBusy(true);

    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: 'user', content: q };
    setMessages((prev) => [...prev, userMsg]);
    setPrompt('');

    try {
      const result = await copilotAsk(q, accessToken, agentTemplate);
      const assistantMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: result.answer,
        provider: result.routedAgent
          ? `WORKER:${result.routedAgent.label}`
          : result.answerProvider,
        routedAgent: result.routedAgent?.label,
        runtimeTools: result.runtime?.toolsUsed,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      onResult?.(result.answer);
      loadMeta();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Copilot request failed';
      setError(msg);
      setMessages((prev) => [
        ...prev,
        { id: `e-${Date.now()}`, role: 'system', content: msg },
      ]);
      onResult?.(msg);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  async function runOnCursor() {
    const q = prompt.trim();
    if (!q || busy || !cursorConnected) return;
    setBusy(true);
    setError(null);
    try {
      const result = await dispatchCursorCloudBuild(
        { spec: q, cursorPrompt: q },
        accessToken,
      );
      const msg = `Cursor agent ${result.mode === 'follow_up' ? 'resumed' : 'started'}: ${result.agentUrl}`;
      setMessages((prev) => [
        ...prev,
        { id: `u-${Date.now()}`, role: 'user', content: `[Cursor task] ${q}` },
        { id: `a-${Date.now()}`, role: 'assistant', content: msg, provider: 'CURSOR' },
      ]);
      setPrompt('');
      onResult?.(msg);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Cursor dispatch failed';
      setError(msg);
      onResult?.(msg);
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void sendMessage(prompt);
    }
  }

  function clearChat() {
    setMessages([]);
    sessionStorage.removeItem(STORAGE_KEY);
  }

  const statusLabel = llmConnected
    ? `Chat LLM · ${defaultProvider.replace('_', ' ')}`
    : defaultProvider === 'CURSOR' && cursorConnected
      ? 'Cursor agent connected · chat uses memory · Run on Cursor for code'
      : 'Rule-based · add DeepSeek/OpenAI in Builder for AI chat';

  const isHero = variant === 'hero';
  const isEmbedded = variant === 'embedded';

  return (
    <section
      className={`flex flex-col overflow-hidden rounded-2xl border shadow-xl ${
        isHero
          ? 'border-violet-500/30 bg-gradient-to-b from-violet-950/25 to-[#0d0d0f]'
          : isEmbedded
            ? 'border-zinc-800/80 bg-[#0d0d0f]'
            : 'border-zinc-800 bg-[#0d0d0f]'
      }`}
    >
      {isHero && memory && (
        <div className="border-b border-violet-500/20 px-5 py-5 sm:px-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-violet-300/80">
            Founder Copilot
          </p>
          <h2 className="mt-2 text-xl font-semibold text-white sm:text-2xl">
            {memory.welcomeMessage.split('\n')[0]}
          </h2>
          <p className="mt-1 text-sm text-zinc-400">
            Your copilot uses project memory, GitHub, and tasks — connected to your stack.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {QUICK_ACTIONS.map((a) => (
              <button
                key={a.label}
                type="button"
                disabled={busy}
                onClick={() => sendMessage(a.prompt)}
                className="rounded-xl border border-zinc-700/80 bg-zinc-900/50 px-3 py-2 text-[11px] font-medium text-zinc-200 transition hover:border-violet-500/50 hover:bg-violet-950/30 hover:text-white disabled:opacity-50 sm:text-xs"
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0">
          {!isHero && !isEmbedded && (
            <>
              <p className="text-sm font-semibold text-zinc-100">Founder Copilot</p>
              <p className="truncate text-xs text-zinc-500">
                {memory?.project?.name ?? 'Project'} ·{' '}
                {memory?.currentGoal?.slice(0, 48) ?? 'Set a goal in Builder settings'}
              </p>
            </>
          )}
          {isEmbedded && (
            <p className="text-sm font-semibold text-zinc-100">Founder Copilot</p>
          )}
          {isHero && (
            <p className="text-sm text-zinc-400">
              {memory?.project?.name ?? 'Project'} · {memory?.progressPercent ?? 0}% · launch{' '}
              {memory?.launchReadiness ?? 0}/100
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-md bg-zinc-900 px-2 py-1 text-[10px] text-zinc-400">{statusLabel}</span>
          <Link href="/settings/builder" className="text-[10px] text-violet-400 hover:underline">
            Settings
          </Link>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={clearChat}
              className="text-[10px] text-zinc-600 hover:text-zinc-400"
            >
              Clear
            </button>
          )}
        </div>
      </header>

      <div ref={scrollRef} className="flex min-h-[320px] max-h-[min(58vh,520px)] flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !busy && (
          <p className="mx-auto max-w-lg text-center text-sm text-zinc-500">
            Ask Founder Brain — answers use GitHub, tasks, roadmap, and build feed context.
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[92%] rounded-lg px-3 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'bg-violet-600/90 text-white'
                  : m.role === 'system'
                    ? 'border border-red-500/30 bg-red-950/20 text-red-200'
                    : 'border border-zinc-800 bg-zinc-900/80 text-zinc-200'
              }`}
            >
              {m.role === 'assistant' && m.provider && (
                <p className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                  {m.routedAgent
                    ? `${m.routedAgent} worker · tasks queued${
                        m.runtimeTools?.includes('github_issues') ? ' · GitHub' : ''
                      }${m.runtimeTools?.includes('cursor_agent') ? ' · Cursor' : ''}`
                    : m.provider === 'RULE_BASED'
                      ? 'Project memory'
                      : m.provider === 'CURSOR'
                        ? 'Cursor + memory'
                        : m.provider === 'PHALA'
                          ? 'Private AI (Phala TEE)'
                          : m.provider.startsWith('WORKER:')
                            ? `${m.provider.replace('WORKER:', '')} worker`
                            : m.provider.replace('_', ' ')}
                </p>
              )}
              {m.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-500">
              Thinking…
            </div>
          </div>
        )}
        {!busy && (
          <div className="flex flex-wrap justify-center gap-2 border-t border-zinc-800/60 pt-3">
            {ASK_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                onClick={() => sendMessage(chip)}
                className="rounded-full border border-zinc-700 bg-zinc-900/60 px-3 py-1.5 text-[11px] text-zinc-300 hover:border-violet-500/50 hover:text-white"
              >
                {chip}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <p className="border-t border-zinc-800 px-4 py-2 text-xs text-red-400">{error}</p>
      )}

      <div className="border-t border-zinc-800 bg-[#0a0a0c] p-3">
        <textarea
          ref={inputRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          disabled={busy}
          placeholder={
            isEmbedded
              ? 'Ask Founder Copilot anything…'
              : 'Ask Founder Copilot anything… Enter to send, Shift+Enter for new line'
          }
          className="w-full resize-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-violet-500/50 disabled:opacity-60"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                if (!supported) {
                  setError('Voice not supported — type your message');
                  return;
                }
                toggle(prompt);
              }}
              className={`rounded-lg px-2 py-1.5 text-sm ${
                listening ? 'bg-red-600 text-white' : 'text-zinc-500 hover:bg-zinc-900 hover:text-white'
              }`}
              title="Voice input"
            >
              {listening ? '⏹' : '🎤'}
            </button>
            {listening && <span className="text-[10px] text-red-300">Listening…</span>}
          </div>
          <div className="flex gap-2">
            {cursorConnected && (
              <button
                type="button"
                disabled={busy || !prompt.trim()}
                onClick={runOnCursor}
                className="rounded-lg border border-emerald-600/40 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-950/40 disabled:opacity-50"
              >
                Run on Cursor
              </button>
            )}
            <button
              type="button"
              disabled={busy || !prompt.trim()}
              onClick={() => sendMessage(prompt)}
              className="rounded-lg bg-violet-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {busy ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

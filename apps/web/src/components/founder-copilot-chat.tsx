'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  copilotAsk,
  executeBuildTask,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  ProjectMemory,
} from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

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
  memory?: ProjectMemory | null;
  initialPrompt?: string | null;
  onInitialPromptConsumed?: () => void;
  agentTemplate?: string | null;
};

const ASK_CHIPS = [
  'What should I ship today?',
  'What broke yesterday?',
  'Continue last task',
  'Create PR',
];

function hasChatLlmProvider(
  providers: { key: string; connectMode?: string; connected: boolean }[],
) {
  return providers.some(
    (p) => p.connectMode === 'api_key' && p.connected && p.key !== 'RULE_BASED',
  );
}

function canExecuteRemotely(worker: string | undefined) {
  return worker === 'CURSOR' || worker === 'OPENHANDS';
}

export function FounderCopilotChat({
  accessToken,
  onResult,
  variant = 'default',
  memory: memoryProp,
  initialPrompt,
  onInitialPromptConsumed,
  agentTemplate,
}: FounderCopilotChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [memoryLocal, setMemoryLocal] = useState<ProjectMemory | null>(null);
  const [llmConnected, setLlmConnected] = useState(false);
  const [buildWorker, setBuildWorker] = useState<string>('NONE');
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const consumedPromptRef = useRef<string | null>(null);

  const memory = memoryProp ?? memoryLocal;

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
      const [mem, builder, worker] = await Promise.all([
        memoryProp ? Promise.resolve(memoryProp) : fetchCopilotMemory(accessToken),
        fetchBuilderSettings(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
      ]);
      if (!memoryProp) setMemoryLocal(mem);
      setLlmConnected(hasChatLlmProvider(builder.providers));
      setBuildWorker(worker?.buildWorker ?? 'NONE');
    } catch {
      if (!memoryProp) setMemoryLocal(null);
    }
  }, [accessToken, memoryProp]);

  useEffect(() => {
    void loadMeta();
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
        provider: result.routedAgent ? `WORKER:${result.routedAgent.label}` : result.answerProvider,
        routedAgent: result.routedAgent?.label,
        runtimeTools: result.runtime?.toolsUsed,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      onResult?.(result.answer);
      void loadMeta();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Copilot request failed';
      setError(msg);
      setMessages((prev) => [...prev, { id: `e-${Date.now()}`, role: 'system', content: msg }]);
      onResult?.(msg);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  async function runExecuteTask() {
    const q = prompt.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await executeBuildTask(
        { spec: q, cursorPrompt: q, repository: memory?.repoFullName ?? undefined },
        accessToken,
      );
      let msg: string;
      if (result.status === 'dispatched' && result.agentUrl) {
        msg = `Builder agent started — track progress in Agents.\n${result.agentUrl}`;
      } else if (result.status === 'error') {
        msg = result.error ?? 'Execute failed — check Settings.';
      } else {
        msg = result.message ?? 'Task queued. Connect a builder worker in Settings for remote execution.';
      }
      setMessages((prev) => [
        ...prev,
        { id: `u-${Date.now()}`, role: 'user', content: q },
        { id: `a-${Date.now()}`, role: 'assistant', content: msg, provider: 'BUILDER' },
      ]);
      setPrompt('');
      onResult?.(msg);
      void loadMeta();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Execute failed';
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
    ? 'Copilot · connected'
    : canExecuteRemotely(buildWorker)
      ? 'Builder agent · ready'
      : 'Connect stack in Settings';

  const isHero = variant === 'hero';
  const isEmbedded = variant === 'embedded';
  const executeReady = canExecuteRemotely(buildWorker);

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
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-zinc-100">Founder Copilot</p>
          {!isHero && (
            <p className="truncate text-xs text-zinc-500">
              {memory?.project?.name ?? 'Project'} · {memory?.currentGoal?.slice(0, 48) ?? 'Set a goal'}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-md bg-zinc-900 px-2 py-1 text-[10px] text-zinc-400">{statusLabel}</span>
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

      <div
        ref={scrollRef}
        className={`flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 ${
          isHero ? 'min-h-[min(62vh,640px)] max-h-[min(72vh,720px)]' : 'min-h-[320px] max-h-[min(58vh,520px)]'
        }`}
      >
        {messages.length === 0 && !busy && (
          <p className="mx-auto max-w-lg text-center text-sm text-zinc-500">
            What should we ship next? Ask anything — or use Execute Task to dispatch your builder agent.
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
                    ? `${m.routedAgent} · tasks queued`
                    : m.provider === 'BUILDER'
                      ? 'Builder agent'
                      : m.provider === 'RULE_BASED'
                        ? 'Project memory'
                        : 'Copilot'}
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
        {!busy && !isHero && (
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
          rows={isHero ? 4 : 3}
          disabled={busy}
          placeholder="Ask Copilot anything…"
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
          </div>
          <div className="flex gap-2">
            {executeReady ? (
              <button
                type="button"
                disabled={busy || !prompt.trim()}
                onClick={() => void runExecuteTask()}
                className="rounded-lg border border-emerald-600/40 bg-emerald-950/30 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-950/50 disabled:opacity-50"
              >
                ▶ Execute Task
              </button>
            ) : (
              <Link
                href={AI_STACK_HREF}
                className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
              >
                Connect builder
              </Link>
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

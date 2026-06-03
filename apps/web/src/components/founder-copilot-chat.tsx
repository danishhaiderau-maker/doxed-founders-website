'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  copilotAsk,
  executeBuildTask,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  ProjectMemory,
  runCopilotAutopilot,
} from '@/lib/api';
import { HybridControlPlane } from '@/components/hybrid-control-plane';
import { useVoiceInput } from '@/hooks/use-voice-input';
import {
  AI_STACK_HREF,
  CopilotSendMode,
  defaultSendMode,
  formatMessageProviderLabel,
  listChatProviders,
  primaryButtonLabel,
  ProviderRow,
  resolveCopilotStack,
  shortProviderName,
} from '@/lib/copilot-ai-stack';
import { updateBuilderSettings } from '@/lib/api';

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
  'Take full control and push all updates',
];

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
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [defaultProvider, setDefaultProvider] = useState('RULE_BASED');
  const [buildWorker, setBuildWorker] = useState('NONE');
  const [sendMode, setSendMode] = useState<CopilotSendMode>('ask');
  const [preferredChatKey, setPreferredChatKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const consumedPromptRef = useRef<string | null>(null);

  const memory = memoryProp ?? memoryLocal;

  const activeChatProvider = preferredChatKey ?? defaultProvider;
  const stack = useMemo(
    () => resolveCopilotStack(providers, activeChatProvider, buildWorker),
    [providers, activeChatProvider, buildWorker],
  );
  const { connected: chatProviderOptions } = useMemo(
    () => listChatProviders(providers, activeChatProvider),
    [providers, activeChatProvider],
  );

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
      setProviders(builder.providers);
      setDefaultProvider(builder.defaultProvider);
      setPreferredChatKey(builder.defaultProvider);
      setBuildWorker(worker?.buildWorker ?? 'NONE');
    } catch {
      if (!memoryProp) setMemoryLocal(null);
    }
  }, [accessToken, memoryProp]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    setSendMode(defaultSendMode(stack));
  }, [stack.canAsk, stack.canBuild]);

  const submit = useCallback(
    async (text: string, mode: CopilotSendMode) => {
      const q = text.trim();
      if (!q || busy) return;
      stop();
      setError(null);
      setBusy(true);

      const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: 'user', content: q };
      setMessages((prev) => [...prev, userMsg]);
      setPrompt('');

      if (/take full control|sync everything|push all updates/i.test(q)) {
        try {
          const result = await runCopilotAutopilot(q, accessToken);
          setMessages((prev) => [
            ...prev,
            {
              id: `a-${Date.now()}`,
              role: 'assistant',
              content: result.answer,
              provider: 'FOUNDER_OS',
            },
          ]);
          onResult?.(result.answer);
          void loadMeta();
        } catch (err) {
          const msg = err instanceof Error ? err.message : 'Autopilot failed';
          setError(msg);
          setMessages((prev) => [...prev, { id: `e-${Date.now()}`, role: 'system', content: msg }]);
          onResult?.(msg);
        } finally {
          setBusy(false);
          inputRef.current?.focus();
        }
        return;
      }

      const useBuild = mode === 'build' && stack.canBuild;

      try {
        if (useBuild) {
          const result = await executeBuildTask(
            { spec: q, cursorPrompt: q, repository: memory?.repoFullName ?? undefined },
            accessToken,
          );
          let msg: string;
          let provider = 'BUILDER';
          if (result.status === 'dispatched' && result.agentUrl) {
            msg = `${stack.buildLabel} agent started — work runs in Cursor/OpenHands, not in this chat. Track progress:\n${result.agentUrl}`;
            provider = result.worker ?? 'BUILDER';
          } else if (result.status === 'error') {
            msg = result.error ?? 'Run failed — check AI Stack settings.';
          } else {
            msg =
              result.message ??
              'Task queued. Connect Cursor or OpenHands in AI Stack to run code on your repo.';
          }
          setMessages((prev) => [
            ...prev,
            { id: `a-${Date.now()}`, role: 'assistant', content: msg, provider },
          ]);
          onResult?.(msg);
        } else {
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
        }
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
    },
    [accessToken, agentTemplate, busy, loadMeta, memory?.repoFullName, onResult, stack.buildLabel, stack.canBuild, stop],
  );

  useEffect(() => {
    if (!initialPrompt?.trim() || busy) return;
    if (consumedPromptRef.current === initialPrompt) return;
    consumedPromptRef.current = initialPrompt;
    void submit(initialPrompt, sendMode);
    onInitialPromptConsumed?.();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot quick action from dashboard
  }, [initialPrompt, busy]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit(prompt, sendMode);
    }
  }

  function clearChat() {
    setMessages([]);
    sessionStorage.removeItem(STORAGE_KEY);
  }

  const isHero = variant === 'hero';
  const isEmbedded = variant === 'embedded';
  const showModeToggle = stack.canAsk && stack.canBuild;
  const buttonLabel = primaryButtonLabel(sendMode, stack);
  const placeholder =
    sendMode === 'build' && stack.canBuild
      ? `Tell ${stack.buildLabel} what to implement in your repo…`
      : stack.canAsk
        ? `Ask ${stack.askLabel} about your project…`
        : 'Ask about your project (uses project memory until you connect an LLM)…';

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
          <Link
            href={AI_STACK_HREF}
            title={stack.statusLine}
            className="max-w-[min(100%,280px)] truncate rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-400 hover:border-violet-500/40 hover:text-violet-200"
          >
            {stack.statusLine}
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

      {!isHero && (
        <div className="border-b border-zinc-800 px-3 py-2">
          <HybridControlPlane
            accessToken={accessToken}
            onMessage={onResult}
            onRefresh={() => void loadMeta()}
            autoRunWhenAutopilot={false}
          />
        </div>
      )}

      <div
        ref={scrollRef}
        className={`flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 ${
          isHero ? 'min-h-[min(62vh,640px)] max-h-[min(72vh,720px)]' : 'min-h-[320px] max-h-[min(58vh,520px)]'
        }`}
      >
        {messages.length === 0 && !busy && (
          <div className="mx-auto max-w-lg space-y-2 text-center text-sm text-zinc-500">
            <p>One box, two roles — pick who handles your message:</p>
            <ul className="text-left text-xs text-zinc-600">
              <li>
                <span className="text-violet-300">Ask</span> — answers here using{' '}
                {stack.canAsk ? stack.askLabel : 'project memory'} (DeepSeek does not edit your repo).
              </li>
              <li>
                <span className="text-emerald-300">Run in repo</span> — dispatches{' '}
                {stack.canBuild ? stack.buildLabel : 'Cursor/OpenHands'} to code on GitHub; open the agent link to watch.
              </li>
            </ul>
          </div>
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
              {m.role === 'assistant' && (
                <p className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                  {formatMessageProviderLabel(m.provider, m.routedAgent)}
                </p>
              )}
              {m.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-500">
              {sendMode === 'build' ? `Dispatching ${stack.buildLabel}…` : 'Thinking…'}
            </div>
          </div>
        )}
        {!busy && !isHero && (
          <div className="flex flex-wrap justify-center gap-2 border-t border-zinc-800/60 pt-3">
            {ASK_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                onClick={() => {
                  const mode =
                    /take full control|sync everything|push all updates/i.test(chip)
                      ? 'ask'
                      : /continue|create pr|finish|implement|fix|ship/i.test(chip) && stack.canBuild
                        ? 'build'
                        : 'ask';
                  if (mode === 'build') setSendMode('build');
                  void submit(chip, mode);
                }}
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
        {chatProviderOptions.length > 1 && sendMode === 'ask' && (
          <div className="mb-2">
            <label className="text-[10px] text-zinc-500">Chat with</label>
            <select
              value={activeChatProvider}
              onChange={async (e) => {
                const key = e.target.value;
                setPreferredChatKey(key);
                try {
                  await updateBuilderSettings({ defaultProvider: key }, accessToken);
                } catch {
                  /* keep local selection */
                }
              }}
              className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-xs text-white"
            >
              {chatProviderOptions.map((p) => (
                <option key={p.key} value={p.key}>
                  {shortProviderName(p)}
                </option>
              ))}
            </select>
          </div>
        )}
        {showModeToggle && (
          <div className="mb-2 flex rounded-lg border border-zinc-800 p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setSendMode('ask')}
              className={`flex-1 rounded-md px-2 py-1.5 font-medium transition ${
                sendMode === 'ask'
                  ? 'bg-violet-600 text-white'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              Ask {stack.askLabel}
            </button>
            <button
              type="button"
              onClick={() => setSendMode('build')}
              className={`flex-1 rounded-md px-2 py-1.5 font-medium transition ${
                sendMode === 'build'
                  ? 'bg-emerald-700 text-white'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              Run in {stack.buildLabel}
            </button>
          </div>
        )}
        <textarea
          ref={inputRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={isHero ? 4 : 3}
          disabled={busy}
          placeholder={placeholder}
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
            {!stack.canAsk && !stack.canBuild && (
              <Link
                href={AI_STACK_HREF}
                className="text-[11px] text-violet-400 hover:underline"
              >
                Connect AI Stack
              </Link>
            )}
          </div>
          <button
            type="button"
            disabled={busy || !prompt.trim()}
            onClick={() => void submit(prompt, sendMode)}
            className={`rounded-lg px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-50 ${
              sendMode === 'build' && stack.canBuild
                ? 'bg-emerald-600 hover:bg-emerald-500'
                : 'bg-violet-600 hover:bg-violet-500'
            }`}
          >
            {busy ? '…' : buttonLabel}
          </button>
        </div>
      </div>
    </section>
  );
}

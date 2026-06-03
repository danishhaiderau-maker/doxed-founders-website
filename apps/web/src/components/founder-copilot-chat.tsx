'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  copilotAsk,
  executeBuildTask,
  fetchBuilderCursorRun,
  fetchBuilderOpenHandsRun,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotMemory,
  ProjectMemory,
  runCopilotAutopilot,
} from '@/lib/api';
import {
  formatBuilderRunInChat,
  formatOpenHandsRunInChat,
  pollCursorRunInChat,
  pollOpenHandsRunInChat,
  type BuilderRunSnapshot,
  type OpenHandsRunSnapshot,
} from '@/lib/builder-run-live';
import { formatThinkingInChat, revealTextInChat } from '@/lib/copilot-inline-stream';
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
  /** Optional deep link when full stream is on cursor.com */
  builderAgentUrl?: string | null;
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
  const [workerConnections, setWorkerConnections] = useState<{
    cursor: boolean;
    openHands: boolean;
  }>({ cursor: false, openHands: false });
  const [sendMode, setSendMode] = useState<CopilotSendMode>('ask');
  const [preferredChatKey, setPreferredChatKey] = useState<string | null>(null);
  const [preferredBuildWorker, setPreferredBuildWorker] = useState<'CURSOR' | 'OPENHANDS' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [thinkingLabel, setThinkingLabel] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const consumedPromptRef = useRef<string | null>(null);

  const memory = memoryProp ?? memoryLocal;

  const activeChatProvider = preferredChatKey ?? defaultProvider;
  const stack = useMemo(
    () => resolveCopilotStack(providers, activeChatProvider, buildWorker, workerConnections),
    [providers, activeChatProvider, buildWorker, workerConnections],
  );
  const { connected: chatProviderOptions } = useMemo(
    () => listChatProviders(providers, activeChatProvider),
    [providers, activeChatProvider],
  );

  const askProviderLabel = useMemo(() => {
    const row = providers.find((p) => p.key === activeChatProvider);
    return row ? shortProviderName(row) : stack.askLabel;
  }, [providers, activeChatProvider, stack.askLabel]);

  const patchMessage = useCallback((id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  const streamAssistantAnswer = useCallback(
    async (
      assistantId: string,
      answer: string,
      meta: Pick<ChatMessage, 'provider' | 'routedAgent' | 'runtimeTools' | 'builderAgentUrl'>,
    ) => {
      await revealTextInChat(answer, (partial) => {
        patchMessage(assistantId, { ...meta, content: partial });
      });
    },
    [patchMessage],
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
      const conn = worker?.connections ?? { cursor: false, openHands: false };
      setWorkerConnections({ cursor: conn.cursor, openHands: conn.openHands });
      const bw = worker?.buildWorker ?? 'NONE';
      setBuildWorker(bw);
      const opts = worker?.buildWorkerOptions ?? [];
      if (opts.length > 0) {
        setPreferredBuildWorker((prev) => {
          const pick =
            (prev && opts.some((o) => o.key === prev) ? prev : null) ??
            opts.find((o) => o.key === bw)?.key ??
            opts[0].key;
          setBuildWorker(pick);
          return pick;
        });
      }
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

  const pollCursorIntoMessage = useCallback(
    async (assistantId: string, agentId: string, runId: string, task: string, mode?: string) => {
      const workerLabel = stack.buildLabel;
      await pollCursorRunInChat(agentId, runId, accessToken, fetchBuilderCursorRun, (snap) => {
        patchMessage(assistantId, {
          content: formatBuilderRunInChat({
            workerLabel,
            task,
            repo: memory?.repoFullName,
            snapshot: snap as BuilderRunSnapshot,
            mode,
          }),
        });
      });
    },
    [accessToken, memory?.repoFullName, patchMessage, stack.buildLabel],
  );

  const pollOpenHandsIntoMessage = useCallback(
    async (assistantId: string, conversationId: string, task: string, agentUrl?: string | null) => {
      const workerLabel = stack.buildLabel;
      await pollOpenHandsRunInChat(conversationId, accessToken, fetchBuilderOpenHandsRun, (snap) => {
        patchMessage(assistantId, {
          content: formatOpenHandsRunInChat({
            workerLabel,
            task,
            repo: memory?.repoFullName,
            snapshot: snap as OpenHandsRunSnapshot,
          }),
          builderAgentUrl: snap.conversationUrl ?? agentUrl ?? null,
        });
      });
    },
    [accessToken, memory?.repoFullName, patchMessage, stack.buildLabel],
  );

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
        const assistantId = `a-${Date.now()}`;
        setThinkingLabel('Founder OS');
        setMessages((prev) => [
          ...prev,
          {
            id: assistantId,
            role: 'assistant',
            content: formatThinkingInChat('Founder OS'),
            provider: 'FOUNDER_OS',
          },
        ]);
        try {
          const result = await runCopilotAutopilot(q, accessToken);
          await streamAssistantAnswer(assistantId, result.answer, { provider: 'FOUNDER_OS' });
          onResult?.(result.answer);
          void loadMeta();
        } catch (err) {
          const msg = err instanceof Error ? err.message : 'Autopilot failed';
          setError(msg);
          setMessages((prev) => [...prev, { id: `e-${Date.now()}`, role: 'system', content: msg }]);
          onResult?.(msg);
        } finally {
          setBusy(false);
          setThinkingLabel(null);
          inputRef.current?.focus();
        }
        return;
      }

      const useBuild = mode === 'build' && stack.canBuild;

      try {
        if (useBuild) {
          setThinkingLabel(stack.buildLabel);
          const workerKey =
            preferredBuildWorker && stack.buildWorkers.some((w) => w.key === preferredBuildWorker)
              ? preferredBuildWorker
              : (stack.buildWorker as 'CURSOR' | 'OPENHANDS' | undefined);
          const result = await executeBuildTask(
            {
              spec: q,
              cursorPrompt: q,
              repository: memory?.repoFullName ?? undefined,
              worker: workerKey,
            },
            accessToken,
          );
          const provider = result.worker ?? 'BUILDER';
          const workerLabel =
            stack.buildWorkers.find((w) => w.key === result.worker)?.label ?? stack.buildLabel;

          if (
            result.status === 'dispatched' &&
            result.worker === 'CURSOR' &&
            result.agentId &&
            result.runId
          ) {
            const assistantId = `a-${Date.now()}`;
            const initial = formatBuilderRunInChat({
              workerLabel,
              task: q,
              repo: memory?.repoFullName,
              snapshot: {
                id: result.runId,
                agentId: result.agentId,
                status: 'CREATING',
              },
              mode: result.mode,
            });
            setMessages((prev) => [
              ...prev,
              {
                id: assistantId,
                role: 'assistant',
                content: initial,
                provider: 'CURSOR',
                builderAgentUrl: result.agentUrl,
              },
            ]);
            onResult?.(initial);
            await pollCursorIntoMessage(assistantId, result.agentId, result.runId, q, result.mode);
          } else if (
            result.status === 'dispatched' &&
            result.worker === 'OPENHANDS' &&
            (result.conversationId ?? result.openHands?.conversationId)
          ) {
            const conversationId =
              result.conversationId ?? result.openHands?.conversationId ?? '';
            const assistantId = `a-${Date.now()}`;
            const initial = formatOpenHandsRunInChat({
              workerLabel,
              task: q,
              repo: memory?.repoFullName,
              snapshot: {
                conversationId,
                status: 'WORKING',
              },
            });
            setMessages((prev) => [
              ...prev,
              {
                id: assistantId,
                role: 'assistant',
                content: initial,
                provider: 'OPENHANDS',
                builderAgentUrl: result.agentUrl,
              },
            ]);
            onResult?.(initial);
            await pollOpenHandsIntoMessage(assistantId, conversationId, q, result.agentUrl);
          } else if (result.status === 'dispatched') {
            const msg = [
              `**${workerLabel}** started on your repo.`,
              result.agentUrl ? `\nSession: ${result.agentUrl}` : '',
              '',
              '_Live output will appear here when the worker reports status._',
            ].join('\n');
            setMessages((prev) => [
              ...prev,
              {
                id: `a-${Date.now()}`,
                role: 'assistant',
                content: msg,
                provider,
                builderAgentUrl: result.agentUrl,
              },
            ]);
            onResult?.(msg);
          } else if (result.status === 'error') {
            const msg = result.error ?? 'Run failed — check AI Stack settings.';
            setMessages((prev) => [
              ...prev,
              { id: `a-${Date.now()}`, role: 'assistant', content: msg, provider },
            ]);
            onResult?.(msg);
          } else {
            const msg =
              result.message ??
              'Task queued. Connect Cursor or OpenHands in AI Stack to run code on your repo.';
            setMessages((prev) => [
              ...prev,
              { id: `a-${Date.now()}`, role: 'assistant', content: msg, provider },
            ]);
            onResult?.(msg);
          }
        } else {
          const assistantId = `a-${Date.now()}`;
          const routedLabel = agentTemplate
            ? providers.find((p) => p.key === agentTemplate)?.label
            : undefined;
          setThinkingLabel(routedLabel ?? askProviderLabel);
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: 'assistant',
              content: formatThinkingInChat(askProviderLabel, routedLabel),
              provider: activeChatProvider,
            },
          ]);

          const result = await copilotAsk(q, accessToken, agentTemplate);
          const providerKey = result.routedAgent
            ? `WORKER:${result.routedAgent.label}`
            : (result.answerProvider ?? activeChatProvider);
          const providerMeta = {
            provider: providerKey,
            routedAgent: result.routedAgent?.label,
            runtimeTools: result.runtime?.toolsUsed,
            builderAgentUrl: result.runtime?.cursorAgentUrl ?? null,
          };

          await streamAssistantAnswer(assistantId, result.answer, providerMeta);
          onResult?.(result.answer);

          if (
            result.runtime?.cursorDispatched &&
            result.runtime.cursorAgentId &&
            result.runtime.cursorRunId
          ) {
            const workerLabel = stack.buildWorkers.find((w) => w.key === 'CURSOR')?.label ?? 'Cursor';
            patchMessage(assistantId, {
              content: formatBuilderRunInChat({
                workerLabel,
                task: q,
                repo: memory?.repoFullName,
                snapshot: {
                  id: result.runtime.cursorRunId,
                  agentId: result.runtime.cursorAgentId,
                  status: 'CREATING',
                },
                mode: result.runtime.cursorMode,
              }),
              provider: 'CURSOR',
            });
            await pollCursorIntoMessage(
              assistantId,
              result.runtime.cursorAgentId,
              result.runtime.cursorRunId,
              q,
              result.runtime.cursorMode,
            );
          }
        }
        void loadMeta();
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Copilot request failed';
        setError(msg);
        setMessages((prev) => [...prev, { id: `e-${Date.now()}`, role: 'system', content: msg }]);
        onResult?.(msg);
      } finally {
        setBusy(false);
        setThinkingLabel(null);
        inputRef.current?.focus();
      }
    },
    [
      accessToken,
      activeChatProvider,
      agentTemplate,
      askProviderLabel,
      busy,
      loadMeta,
      memory?.repoFullName,
      onResult,
      patchMessage,
      pollCursorIntoMessage,
      pollOpenHandsIntoMessage,
      preferredBuildWorker,
      providers,
      stack.buildLabel,
      stack.buildWorker,
      stack.buildWorkers,
      stack.canBuild,
      stop,
      streamAssistantAnswer,
    ],
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
                <span className="text-violet-300">Ask</span> —{' '}
                {stack.canAsk
                  ? `streams answers inline from ${stack.chatProviders.map((p) => p.label).join(', ')}`
                  : 'uses project memory until you connect an LLM'}
                .
              </li>
              <li>
                <span className="text-emerald-300">Run in repo</span> —{' '}
                {stack.canBuild
                  ? stack.buildWorkers.map((w) => w.label).join(' and ')
                  : 'Cursor or OpenHands'}{' '}
                stream code status and output here (no need to leave Mission Control).
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
              {m.builderAgentUrl ? (
                <p className="mt-2 border-t border-zinc-800/80 pt-2 text-[10px] text-zinc-500">
                  <a
                    href={m.builderAgentUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-emerald-400/90 hover:underline"
                  >
                    Open full session ↗
                  </a>
                  {' · '}live output streams in Mission Control
                </p>
              ) : null}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-500">
              {sendMode === 'build'
                ? `Running ${thinkingLabel ?? stack.buildLabel} on your repo…`
                : `${thinkingLabel ?? askProviderLabel} · streaming answer…`}
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
        {stack.buildWorkers.length > 1 && sendMode === 'build' && (
          <div className="mb-2">
            <label className="text-[10px] text-zinc-500">Code with</label>
            <select
              value={preferredBuildWorker ?? stack.buildWorker}
              onChange={(e) => {
                const key = e.target.value as 'CURSOR' | 'OPENHANDS';
                setPreferredBuildWorker(key);
                setBuildWorker(key);
              }}
              className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-xs text-white"
            >
              {stack.buildWorkers.map((w) => (
                <option key={w.key} value={w.key}>
                  {w.label}
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
        {!showModeToggle && stack.canBuild && !stack.canAsk && stack.buildWorkers.length > 1 && (
          <p className="mb-2 text-[10px] text-zinc-500">
            Code agent: <span className="text-emerald-300">{stack.buildLabel}</span>
          </p>
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

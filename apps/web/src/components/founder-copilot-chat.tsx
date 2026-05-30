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
};

export function FounderCopilotChat({ accessToken, onResult }: FounderCopilotChatProps) {
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
      setLlmConnected(
        builder.providers.some((p) => p.needsApiKey && p.connected && p.key !== 'RULE_BASED'),
      );
    } catch {
      setMemory(null);
    }
  }, [accessToken]);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

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
      const result = await copilotAsk(q, accessToken);
      const assistantMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: result.answer,
        provider: result.answerProvider,
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
    ? `LLM connected${defaultProvider === 'CURSOR' ? ' · chat uses your API key' : ''}`
    : defaultProvider === 'CURSOR' && cursorConnected
      ? 'Cursor agent · use Run on Cursor for code tasks'
      : 'Rule-based fallback · add API key in Builder settings';

  return (
    <section className="flex flex-col overflow-hidden rounded-xl border border-zinc-800 bg-[#0d0d0f] shadow-xl">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-zinc-100">Founder Copilot</p>
          <p className="truncate text-xs text-zinc-500">
            {memory?.project?.name ?? 'Project'} · {memory?.currentGoal?.slice(0, 48) ?? 'Set a goal in Builder settings'}
          </p>
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
          <div className="mx-auto max-w-md text-center text-sm text-zinc-500">
            <p>Ask anything about your repo, tasks, or next step.</p>
            <p className="mt-2 text-xs">
              Examples: &quot;What am I working on?&quot; · &quot;Summarize last commits&quot; · &quot;What should I ship today?&quot;
            </p>
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
              {m.role === 'assistant' && m.provider && (
                <p className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                  {m.provider === 'RULE_BASED' ? 'Rule-based' : m.provider.replace('_', ' ')}
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
          placeholder="Message Founder Copilot… Enter to send, Shift+Enter for new line"
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

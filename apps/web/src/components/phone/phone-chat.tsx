'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Send } from 'lucide-react';
import { apiUrl } from '@/lib/api-base';
import { dispatchToIdeSession, fetchIdeDispatchStatus } from '@/lib/api';
import {
  PHONE_MODEL_ALIASES,
  type PhoneChatMessage,
  type PhoneModelId,
  type FounderOsRouteMetadata,
  type ConnectedNode,
} from './types';

type Props = {
  accessToken: string;
  /** The IDE the phone is currently controlling (shown in the chat header). */
  activeNode: ConnectedNode | null;
};

type PhoneMode = 'ai' | 'ide';

const DISPATCH_POLL_MS = 1_500;
const DISPATCH_TIMEOUT_MS = 75_000;

/**
 * Phone chat with SSE streaming through the AI Gateway.
 *
 * Calls POST /api/v1/chat/phone-completions (JWT-authenticated) with
 * `stream: true` and `founder_os_metadata: true`. The server emits a leading
 * `data: {"founderOs":{...}}\n\n` line carrying the route decision (tier /
 * provider / model / DDollar cost), then standard OpenAI `choices[0].delta`
 * chunks, then `data: [DONE]`. We parse the SSE stream incrementally so tokens
 * render as they arrive — same routing path as the desktop IDE. See
 * ai-proxy.controller.ts §chatPhoneCompletions and
 * docs/FOUNDER-IDE-FORK-PLAN.md §8.2.
 */
export function PhoneChat({ accessToken, activeNode }: Props) {
  const [messages, setMessages] = useState<PhoneChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [model, setModel] = useState<PhoneModelId>('founder-os-auto');
  const [mode, setMode] = useState<PhoneMode>('ai');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setError(null);

    const userMsg: PhoneChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
    };
    const assistantId = `a-${Date.now()}`;
    const assistantMsg: PhoneChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      streaming: true,
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput('');
    setSending(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      if (mode === 'ide') {
        if (!activeNode || activeNode.status !== 'online') {
          throw new Error('Choose an online Founder IDE before sending remote work.');
        }
        const created = await dispatchToIdeSession(
          accessToken,
          `founder-ide:${activeNode.nodeId}`,
          text,
          'founder-ide',
        );
        const deadline = Date.now() + DISPATCH_TIMEOUT_MS;
        let lastStatus = created.status;
        while (Date.now() < deadline) {
          if (controller.signal.aborted) throw new DOMException('Stopped', 'AbortError');
          const status = await fetchIdeDispatchStatus(accessToken, created.id);
          lastStatus = status.status;
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content:
                      status.status === 'DISPATCHING'
                        ? `Founder Node reached ${activeNode.label || 'your laptop'}. Waiting for Founder IDE...`
                        : `Queued securely for ${activeNode.label || 'your laptop'}...`,
                  }
                : message,
            ),
          );
          if (status.failed) throw new Error(remoteFailureMessage(status.result));
          if (status.delivered) {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: `Delivered to ${activeNode.label || 'Founder IDE'}. The request is visible in Founder Chat; edits and commands still require approval.`,
                      streaming: false,
                    }
                  : message,
              ),
            );
            return;
          }
          await delay(DISPATCH_POLL_MS, controller.signal);
        }
        throw new Error(
          lastStatus === 'PENDING'
            ? 'Founder IDE did not come online before the delivery window expired.'
            : 'Founder IDE did not confirm delivery in time.',
        );
      }

      const res = await fetch(apiUrl('/api/v1/chat/phone-completions'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          model,
          stream: true,
          founder_os_metadata: true,
          messages: [
            {
              role: 'system',
              content: buildSystemPrompt(activeNode),
            },
            ...[...messages, userMsg]
              .filter((m) => m.role === 'user' || m.role === 'assistant')
              .map((m) => ({ role: m.role, content: m.content })),
          ],
        }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const errText = await res.text().catch(() => '');
        let message = `Gateway error (${res.status})`;
        try {
          const parsed = JSON.parse(errText);
          message = parsed?.error?.message ?? message;
        } catch {
          if (errText) message = errText.slice(0, 200);
        }
        throw new Error(message);
      }

      await parseSseStream(res.body, {
        onMetadata: (meta) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, route: meta } : m,
            ),
          );
        },
        onDelta: (delta) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + delta } : m,
            ),
          );
        },
      });

      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
      );
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') {
        // user cancelled — finalize the partial message
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
        );
      } else {
        setError(e instanceof Error ? e.message : 'Chat failed');
        setMessages((prev) =>
          prev.filter((m) => m.id !== assistantId),
        );
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }, [accessToken, activeNode, input, messages, mode, model, sending]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return (
    <div className="flex flex-col rounded-2xl border border-zinc-800 bg-zinc-950/50">
      {/* Header — model selector + active IDE */}
      <div className="space-y-2 border-b border-zinc-800/80 px-3 py-2.5">
        <div className="grid grid-cols-2 rounded-lg bg-zinc-900 p-0.5" role="tablist" aria-label="Phone Remote mode">
          {([
            ['ai', 'Founder AI'],
            ['ide', 'Control IDE'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              onClick={() => setMode(value)}
              className={[
                'min-h-8 rounded-md px-2 text-xs font-semibold transition',
                mode === value ? 'bg-zinc-700 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-200',
              ].join(' ')}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center justify-between gap-2">
          {mode === 'ai' ? (
            <select
              value={model}
              onChange={(e) => setModel(e.target.value as PhoneModelId)}
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none"
              aria-label="Execution profile"
            >
              {PHONE_MODEL_ALIASES.map((m) => (
                <option key={m.id} value={m.id} title={m.hint}>
                  {m.label}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-[11px] text-zinc-400">Authenticated Founder Node relay</span>
          )}
          <span className="truncate text-[10px] text-zinc-500">
            {mode === 'ide'
              ? activeNode?.status === 'online'
                ? activeNode.label ?? 'Founder IDE online'
                : 'Choose an online IDE'
              : 'Managed DeepSeek'}
          </span>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="max-h-[52vh] min-h-[40vh] space-y-3 overflow-y-auto px-3 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 py-10 text-center">
            <p className="text-sm text-zinc-500">
              {mode === 'ide' ? 'Send work to the selected Founder IDE.' : 'Chat with Founder AI from your phone.'}
            </p>
            <p className="max-w-xs text-[11px] text-zinc-600">
              {mode === 'ide'
                ? 'Founder Node delivers it to Founder Chat. File changes and commands remain reviewable and fail closed.'
                : 'Same managed routing as the desktop IDE, with the provider and usage receipt shown under each reply.'}
            </p>
          </div>
        ) : (
          messages.map((m) => <MessageBubble key={m.id} message={m} />)
        )}
      </div>

      {error && (
        <div className="mx-3 mb-2 rounded-lg border border-rose-500/30 bg-rose-950/15 px-3 py-2 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      {/* Composer */}
      <div className="flex items-end gap-2 border-t border-zinc-800/80 p-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={1}
          placeholder={mode === 'ide' ? 'Ask Founder IDE to review or change something...' : 'Message Founder AI...'}
          className="max-h-32 min-h-[2.5rem] flex-1 resize-none rounded-xl border border-zinc-800 bg-black px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
        />
        {sending ? (
          <button
            type="button"
            onClick={stop}
            className="rounded-xl border border-zinc-700 px-3 py-2 text-xs font-semibold text-zinc-200 hover:border-zinc-500"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void send()}
            disabled={!input.trim() || (mode === 'ide' && activeNode?.status !== 'online')}
            className="rounded-xl bg-emerald-500 p-2.5 text-black disabled:opacity-40"
            aria-label="Send"
          >
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

function remoteFailureMessage(result: string | null): string {
  if (!result) return 'Founder IDE rejected or could not deliver the request.';
  try {
    const parsed = JSON.parse(result) as { error?: unknown };
    if (typeof parsed.error === 'string' && parsed.error.trim()) {
      return `Founder IDE could not deliver the request: ${parsed.error.slice(0, 180)}`;
    }
  } catch {
    // Older bridge results are plain text.
  }
  return `Founder IDE could not deliver the request: ${result.replace(/^error:\s*/i, '').slice(0, 180)}`;
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('Stopped', 'AbortError'));
      },
      { once: true },
    );
  });
}

function MessageBubble({ message }: { message: PhoneChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={isUser ? 'flex flex-col items-end' : 'flex flex-col items-start'}>
      <div
        className={[
          'max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-sm',
          isUser
            ? 'bg-emerald-500 text-black'
            : 'border border-zinc-800 bg-zinc-900/60 text-zinc-100',
        ].join(' ')}
      >
        {message.content || (message.streaming ? '…' : '')}
        {message.streaming && <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-emerald-400 align-middle" />}
      </div>
      {message.route && (
        <RouteBadge route={message.route} />
      )}
    </div>
  );
}

function RouteBadge({ route }: { route: FounderOsRouteMetadata }) {
  return (
    <div className="mt-1 flex flex-wrap gap-1 text-[9px] font-semibold uppercase tracking-wider">
      <span className="rounded-full bg-zinc-800/80 px-1.5 py-0.5 text-zinc-300">{route.tier}</span>
      <span className="rounded-full bg-violet-500/15 px-1.5 py-0.5 text-violet-200">{route.provider}</span>
      <span className="rounded-full bg-zinc-800/80 px-1.5 py-0.5 text-zinc-400">{route.model}</span>
      <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-amber-200">{route.ddollarCost} DD</span>
    </div>
  );
}

function buildSystemPrompt(activeNode: ConnectedNode | null): string {
  const lines = [
    'You are Founder OS AI, assisting a founder from their phone via the Phone Remote UI.',
    'Be concise and actionable — the founder is on a small screen.',
  ];
  if (activeNode) {
    lines.push(
      `The founder is currently controlling the IDE/node "${activeNode.label || 'Unnamed machine'}" (${activeNode.platform ?? 'desktop'}, ${activeNode.status}).`,
    );
  }
  return lines.join('\n');
}

/**
 * Incremental SSE parser. Splits the incoming ReadableStream on newlines,
 * extracts `data: ` payloads, and dispatches `founderOs` metadata lines and
 * OpenAI `choices[0].delta.content` deltas. Terminates on `data: [DONE]`.
 */
async function parseSseStream(
  body: ReadableStream<Uint8Array>,
  handlers: {
    onMetadata: (meta: FounderOsRouteMetadata) => void;
    onDelta: (delta: string) => void;
  },
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') return;
        try {
          const evt = JSON.parse(payload) as Record<string, unknown>;
          const founderOs = evt?.founderOs as FounderOsRouteMetadata | undefined;
          if (founderOs && typeof founderOs === 'object') {
            handlers.onMetadata(founderOs);
            continue;
          }
          const choices = evt?.choices as Array<{ delta?: { content?: string } }> | undefined;
          const delta = choices?.[0]?.delta?.content;
          if (delta) handlers.onDelta(delta);
        } catch {
          // ignore malformed chunk
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // already closed
    }
  }
}

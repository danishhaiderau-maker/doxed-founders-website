'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchMessageConversation,
  fetchMessageThreads,
  sendPlatformMessage,
  type MessageThread,
  type PlatformMessageItem,
} from '@/lib/api';

type Props = {
  accessToken: string;
  initialOtherUserId?: string | null;
};

export function AccountMessagesPanel({ accessToken, initialOtherUserId }: Props) {
  const [threads, setThreads] = useState<MessageThread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(initialOtherUserId ?? null);
  const [messages, setMessages] = useState<PlatformMessageItem[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadThreads = useCallback(async () => {
    try {
      const data = await fetchMessageThreads(accessToken);
      setThreads(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load messages');
    }
  }, [accessToken]);

  const loadConversation = useCallback(
    async (otherUserId: string) => {
      try {
        const data = await fetchMessageConversation(otherUserId, accessToken);
        setMessages(data);
        setErr(null);
        await loadThreads();
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'Could not load conversation');
      }
    },
    [accessToken, loadThreads],
  );

  useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    if (initialOtherUserId) setActiveId(initialOtherUserId);
  }, [initialOtherUserId]);

  useEffect(() => {
    if (activeId) void loadConversation(activeId);
    else setMessages([]);
  }, [activeId, loadConversation]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!activeId || !draft.trim()) return;
    setBusy(true);
    try {
      await sendPlatformMessage(activeId, draft.trim(), accessToken);
      setDraft('');
      await loadConversation(activeId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Send failed');
    } finally {
      setBusy(false);
    }
  }

  const activeThread = threads.find((t) => t.otherUserId === activeId);

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/60">
      <div className="border-b border-zinc-800 px-4 py-3">
        <h3 className="font-semibold text-white">Messages</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Direct messages with scouts and admins. Listing proof requests appear here.
        </p>
      </div>

      <div className="grid min-h-[360px] md:grid-cols-[220px_1fr]">
        <div className="border-b border-zinc-800 md:border-b-0 md:border-r">
          {threads.length === 0 ? (
            <p className="p-4 text-xs text-zinc-500">No conversations yet.</p>
          ) : (
            <ul className="max-h-[400px] overflow-y-auto">
              {threads.map((t) => (
                <li key={t.otherUserId}>
                  <button
                    type="button"
                    onClick={() => setActiveId(t.otherUserId)}
                    className={`w-full px-3 py-2.5 text-left text-xs transition hover:bg-zinc-900 ${
                      activeId === t.otherUserId ? 'bg-zinc-900' : ''
                    }`}
                  >
                    <p className="font-semibold text-white">{t.otherUserLabel}</p>
                    <p className="mt-0.5 line-clamp-2 text-zinc-500">{t.lastBody}</p>
                    {t.unreadCount > 0 && (
                      <span className="mt-1 inline-block rounded-full bg-cyan-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
                        {t.unreadCount} new
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex min-h-[360px] flex-col">
          {!activeId ? (
            <p className="flex flex-1 items-center justify-center p-6 text-sm text-zinc-500">
              Select a conversation
            </p>
          ) : (
            <>
              <div className="border-b border-zinc-800 px-4 py-2">
                <p className="text-sm font-semibold text-white">{activeThread?.otherUserLabel}</p>
                {activeThread?.applicationLabel && (
                  <p className="text-[11px] text-violet-300">Re: {activeThread.applicationLabel}</p>
                )}
              </div>
              <ul className="flex-1 space-y-2 overflow-y-auto p-4">
                {messages.map((m) => (
                  <li
                    key={m.id}
                    className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                      m.mine
                        ? 'ml-auto bg-cyan-900/40 text-cyan-50'
                        : 'bg-zinc-800 text-zinc-200'
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{m.body}</p>
                    <p className="mt-1 text-[10px] opacity-60">
                      {new Date(m.createdAt).toLocaleString()}
                    </p>
                  </li>
                ))}
              </ul>
              <form onSubmit={handleSend} className="border-t border-zinc-800 p-3">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={3}
                  placeholder="Write a reply…"
                  className="w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
                />
                <button
                  type="submit"
                  disabled={busy || !draft.trim()}
                  className="mt-2 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  Send
                </button>
              </form>
            </>
          )}
        </div>
      </div>

      {err && <p className="border-t border-red-500/30 px-4 py-2 text-sm text-red-300">{err}</p>}
    </section>
  );
}

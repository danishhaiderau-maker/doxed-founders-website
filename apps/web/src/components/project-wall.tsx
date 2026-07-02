'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Send, Pin, Sparkles, ShieldCheck, MessageSquare, Loader2 } from 'lucide-react';
import {
  fetchProjectWall,
  joinProjectWall,
  pinWallMessage,
  postProjectWallMessage,
  WALL_PIN_COST_DDOLLAR,
  type WallMessage,
} from '@/lib/api';

function fmtTime(ts: string): string {
  const d = new Date(ts);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `${hh}:${mm}`;
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${hh}:${mm}`;
}

function sourceBadge(source: string): { label: string; className: string } | null {
  if (source === 'build_post') return { label: 'BUILD LOG', className: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30' };
  if (source === 'community_thread' || source === 'social_hub') return { label: 'SOCIAL HUB', className: 'text-violet-300 bg-violet-500/10 border-violet-500/30' };
  return null;
}

function Avatar({ m, mine }: { m: WallMessage; mine: boolean }) {
  const initial = (m.author.name ?? m.author.platformHandle ?? '?').slice(0, 1).toUpperCase();
  if (m.author.avatarUrl) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={m.author.avatarUrl} alt="" className={`h-8 w-8 rounded-full object-cover ${mine ? 'order-2' : ''}`} />;
  }
  return (
    <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs font-bold text-zinc-300 ${mine ? 'order-2' : ''}`}>
      {initial}
    </div>
  );
}

function MessageBubble({
  m,
  mine,
  canPin,
  onPin,
}: {
  m: WallMessage;
  mine: boolean;
  canPin: boolean;
  onPin: (m: WallMessage) => void;
}) {
  const badge = sourceBadge(m.source);
  const pinned = Boolean(m.pin);
  return (
    <div className={`flex items-start gap-2.5 ${mine ? 'flex-row-reverse' : ''}`}>
      <Avatar m={m} mine={mine} />
      <div className={`max-w-[78%] ${mine ? 'items-end text-right' : 'items-start text-left'} flex flex-col`}>
        <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          <span className="font-medium text-zinc-400">
            {m.author.name ?? m.author.platformHandle ?? 'Anon'}
          </span>
          {m.author.isVerifiedFounder && (
            <span className="inline-flex items-center gap-0.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1 py-px text-[9px] font-semibold text-emerald-300">
              <ShieldCheck className="h-2.5 w-2.5" /> FOUNDER
            </span>
          )}
          <span>{fmtTime(m.createdAt)}</span>
          {badge && (
            <span className={`rounded border px-1 py-px text-[8px] font-bold uppercase tracking-wide ${badge.className}`}>
              {badge.label}
            </span>
          )}
        </div>
        <div
          className={`mt-1 rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed ${
            mine
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-50'
              : pinned
                ? 'border-amber-500/40 bg-amber-500/10 text-amber-50'
                : 'border-zinc-700/70 bg-zinc-900/70 text-zinc-100'
          }`}
        >
          {pinned && !mine && (
            <div className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase text-amber-400">
              <Pin className="h-3 w-3" /> Pinned · {m.pin!.kind}
            </div>
          )}
          <p className="whitespace-pre-wrap break-words">{m.body}</p>
        </div>
        {canPin && !pinned && (
          <button
            type="button"
            onClick={() => onPin(m)}
            className="mt-1 inline-flex items-center gap-1 text-[10px] text-violet-400/80 transition hover:text-violet-300"
          >
            <Sparkles className="h-3 w-3" /> Pin · {WALL_PIN_COST_DDOLLAR} DD
          </button>
        )}
      </div>
    </div>
  );
}

function PinConfirmModal({
  message,
  balance,
  onClose,
  onConfirm,
}: {
  message: WallMessage | null;
  balance: number | null;
  onClose: () => void;
  onConfirm: (kind: 'pin' | 'highlight' | 'promote') => void;
}) {
  const [kind, setKind] = useState<'pin' | 'highlight' | 'promote'>('pin');
  if (!message) return null;
  const cost = WALL_PIN_COST_DDOLLAR;
  const affordable = balance == null || balance >= cost;
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-2xl border border-violet-500/40 bg-[#0B0B0B] p-5 shadow-2xl">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-violet-400" />
          <h3 className="text-base font-semibold text-white">Upgrade subtopic</h3>
        </div>
        <p className="mt-3 text-sm text-zinc-400">
          Boost this message to pin it at the top of the project wall. Founders use upgrades to highlight important
          threads, announcements, and questions.
        </p>
        <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-xs text-zinc-300">
          <p className="line-clamp-2 text-zinc-400">“{message.body.slice(0, 120)}…”</p>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          {(['pin', 'highlight', 'promote'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={`rounded-lg border px-3 py-2 text-xs font-medium capitalize transition ${
                kind === k
                  ? 'border-violet-500/60 bg-violet-500/15 text-violet-100'
                  : 'border-zinc-700 text-zinc-400 hover:border-zinc-600'
              }`}
            >
              {k}
            </button>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between text-sm">
          <span className="text-zinc-400">
            Cost: <span className="font-semibold text-amber-300">{cost.toLocaleString()} DDollar</span>
          </span>
          {balance != null && (
            <span className={affordable ? 'text-zinc-500' : 'text-red-400'}>
              Balance: {balance.toLocaleString()} DD
            </span>
          )}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-900">
            Cancel
          </button>
          <button
            type="button"
            disabled={!affordable}
            onClick={() => onConfirm(kind)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Confirm upgrade
          </button>
        </div>
      </div>
    </div>
  );
}

export interface ProjectWallProps {
  slug: string;
  /** When true, the wall is rendered in a compact drawer context (no join CTA, tighter spacing). */
  compact?: boolean;
  /** Optional DDollar balance for the current user (for the pin modal). */
  ddollarBalance?: number | null;
}

export function ProjectWall({ slug, compact = false, ddollarBalance = null }: ProjectWallProps) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [messages, setMessages] = useState<WallMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [joined, setJoined] = useState<boolean | null>(null);
  const [pinTarget, setPinTarget] = useState<WallMessage | null>(null);
  const [pinBusy, setPinBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchProjectWall(slug, token);
      setMessages((prev) => (prev.length === data.length ? data : data));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load wall');
    } finally {
      setLoading(false);
    }
  }, [slug, token]);

  useEffect(() => {
    setLoading(true);
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [load]);

  // Determine whether the current user has joined the project (followed).
  // We infer from the project room endpoint reusing the existing follow state.
  useEffect(() => {
    if (!token) {
      setJoined(null);
      return;
    }
    let cancelled = false;
    fetchProjectWall(slug, token)
      .then(() => {
        // The wall endpoint is public; membership is inferred from a follow probe.
      })
      .catch(() => {});
    // Lightweight membership probe via the join endpoint is not needed; the
    // post endpoint will return 403 if not joined. We set joined=null until
    // the user attempts to post. For the CTA we rely on the project page.
    if (cancelled) return;
    setJoined(null);
    return () => {
      cancelled = true;
    };
  }, [slug, token]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length]);

  const myId = session?.user?.id;

  async function handleSend() {
    const body = draft.trim();
    if (!body || !token) return;
    setSending(true);
    setNotice(null);
    try {
      const msg = await postProjectWallMessage(slug, body, token);
      setMessages((prev) => [...prev, msg]);
      setDraft('');
      setJoined(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to post';
      setError(msg);
      if (/join/i.test(msg)) setJoined(false);
    } finally {
      setSending(false);
    }
  }

  async function handleJoin() {
    if (!token) return;
    setNotice(null);
    try {
      await joinProjectWall(slug, token);
      setJoined(true);
      setNotice('Joined — you can now post on this wall.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Failed to join');
    }
  }

  async function handlePin(kind: 'pin' | 'highlight' | 'promote') {
    if (!token || !pinTarget) return;
    setPinBusy(true);
    try {
      await pinWallMessage(pinTarget.id, token, kind);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pinTarget.id
            ? { ...m, pin: { kind, userId: session?.user?.id ?? '', cost: WALL_PIN_COST_DDOLLAR, createdAt: new Date().toISOString() } }
            : m,
        ),
      );
      setPinTarget(null);
      setNotice(`Subtopic upgraded — ${WALL_PIN_COST_DDOLLAR} DDollar spent.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Failed to upgrade subtopic');
    } finally {
      setPinBusy(false);
    }
  }

  const canPin = Boolean(token);

  if (loading && messages.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/30 py-10 text-sm text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading wall…
      </div>
    );
  }

  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-[#0B0B0B] shadow-[0_0_40px_-12px_rgba(124,58,237,0.35)] ${
        compact ? 'max-h-[60vh]' : 'h-[70vh] min-h-[460px]'
      }`}
    >
      {/* Header strip */}
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950/60 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-white">
          <MessageSquare className="h-4 w-4 text-violet-400" />
          Project wall
          <span className="ml-1 inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-px text-[10px] font-bold text-emerald-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" /> LIVE
          </span>
        </div>
        <span className="text-[11px] text-zinc-500">{messages.length} messages</span>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-xs text-red-300">{error}</p>
        )}
        {messages.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
            <MessageSquare className="h-8 w-8 text-zinc-700" />
            <p className="text-sm text-zinc-500">No messages yet.</p>
            <p className="text-xs text-zinc-600">Be the first to start the conversation on this project wall.</p>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            m={m}
            mine={myId === m.authorId}
            canPin={canPin}
            onPin={setPinTarget}
          />
        ))}
        {/* Typing indicator placeholder */}
        {token && (
          <div className="flex items-center gap-1.5 pl-11 text-[11px] text-zinc-600">
            <span className="flex gap-0.5">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-600 [animation-delay:-0.2s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-600 [animation-delay:-0.1s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-600" />
            </span>
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-zinc-800 bg-zinc-950/60 px-3 py-2.5">
        {notice && <p className="mb-1.5 text-[11px] text-emerald-400">{notice}</p>}
        {!token ? (
          <Link
            href={`/login?callbackUrl=/project/${encodeURIComponent(slug)}`}
            className="block w-full rounded-lg border border-zinc-700 px-3 py-2 text-center text-xs text-zinc-300 transition hover:border-zinc-600 hover:text-white"
          >
            Sign in to join the conversation
          </Link>
        ) : joined === false ? (
          <button
            type="button"
            onClick={handleJoin}
            className="w-full rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-violet-500"
          >
            Join project to post
          </button>
        ) : (
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              rows={1}
              placeholder="Type a message…  (Enter to send, Shift+Enter for newline)"
              className="max-h-32 min-h-[40px] flex-1 resize-none rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500/50 focus:outline-none"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={sending || !draft.trim()}
              className="inline-flex h-10 items-center justify-center gap-1.5 rounded-xl bg-violet-600 px-3 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              <span className="hidden sm:inline">Send</span>
            </button>
          </div>
        )}
      </div>

      <PinConfirmModal
        message={pinTarget}
        balance={ddollarBalance}
        onClose={() => !pinBusy && setPinTarget(null)}
        onConfirm={handlePin}
      />
    </div>
  );
}

'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Send, Pin, Sparkles, ShieldCheck, MessageSquare, Loader2, Lock, Twitter, RefreshCw, Video, Milestone } from 'lucide-react';
import {
  fetchProjectWall,
  fetchWallMembership,
  fetchWallSummary,
  joinProjectWall,
  pinWallMessage,
  postProjectWallMessage,
  activateWallSummarizer,
  WALL_PIN_COST_DDOLLAR,
  WALL_SUMMARIZER_COST_DDOLLAR,
  type WallMessage,
  type WallMembership,
  type WallSummary,
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
  if (source === 'founder_video') return { label: '🎬 Founder video', className: 'text-sky-300 bg-sky-500/10 border-sky-500/30' };
  if (source === 'raise_milestone') return { label: '💰 Raise milestone', className: 'text-amber-300 bg-amber-500/10 border-amber-500/30' };
  if (source !== 'chat') return { label: source.toUpperCase().slice(0, 16), className: 'text-zinc-300 bg-zinc-500/10 border-zinc-500/30' };
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
  const isSystem = m.source !== 'chat';
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
                : isSystem
                  ? 'border-sky-500/30 bg-sky-500/5 text-sky-50'
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

const SENTIMENT_STYLES: Record<NonNullable<WallSummary['sentimentLabel']>, string> = {
  positive: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  neutral: 'border-zinc-600/60 bg-zinc-700/30 text-zinc-300',
  negative: 'border-red-500/40 bg-red-500/10 text-red-300',
};

function WallSummaryCard({
  slug,
  projectName,
  ticker,
  summary,
  origin,
  onRenew,
  renewing,
}: {
  slug: string;
  projectName: string;
  ticker: string;
  summary: WallSummary;
  origin: string;
  onRenew: () => void;
  renewing: boolean;
}) {
  if (!summary.summaryBody) return null;
  const sentiment = summary.sentimentLabel ?? 'neutral';
  const projectUrl = `${origin}/project/${encodeURIComponent(slug)}`;
  const handle = ticker ? `$${ticker}` : projectName;
  const tweetText = `Chat summary for ${handle}: ${summary.summaryBody.slice(0, 180)}${summary.summaryBody.length > 180 ? '…' : ''}\n\nSentiment: ${sentiment}\n${projectUrl}`;
  const tweetHref = `https://twitter.com/intent/tweet?text=${encodeURIComponent(tweetText)}`;
  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-violet-200">
          <Sparkles className="h-3.5 w-3.5 text-violet-400" />
          Chat Summarizer
          {!summary.active && (
            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-px text-[9px] font-bold text-amber-300">
              EXPIRED
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {!summary.active && (
            <button
              type="button"
              onClick={onRenew}
              disabled={renewing}
              className="inline-flex items-center gap-1 rounded-lg border border-violet-500/50 bg-violet-600/20 px-2 py-1 text-[10px] font-semibold text-violet-100 transition hover:bg-violet-600/30 disabled:opacity-50"
            >
              {renewing ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
              Renew · {WALL_SUMMARIZER_COST_DDOLLAR} DD/mo
            </button>
          )}
          <a
            href={tweetHref}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-[10px] font-semibold text-sky-200 transition hover:bg-sky-500/20"
          >
            <Twitter className="h-3 w-3" /> X link
          </a>
        </div>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-zinc-200">{summary.summaryBody}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
        <span className={`rounded-full border px-2 py-0.5 font-semibold uppercase ${SENTIMENT_STYLES[sentiment]}`}>
          {sentiment}
        </span>
        {summary.sentimentReasoning && (
          <span className="text-zinc-400">
            <span className="text-zinc-500">Why:</span> {summary.sentimentReasoning}
          </span>
        )}
      </div>
      {summary.expiresAt && (
        <p className="mt-1.5 text-[10px] text-zinc-600">
          {summary.active ? 'Active until' : 'Expired'} {new Date(summary.expiresAt).toLocaleString()}
        </p>
      )}
    </div>
  );
}

export interface ProjectWallProps {
  slug: string;
  /** When true, the wall is rendered in a compact drawer context (no join CTA, tighter spacing). */
  compact?: boolean;
  /** Optional DDollar balance for the current user (for the pin modal + summarizer). */
  ddollarBalance?: number | null;
  /** When true, the project's founder is verified AND live-trading — summarizer button is shown. */
  summarizerEligible?: boolean;
  /** Origin for building shareable URLs (passed from the project page). */
  shareOrigin?: string;
}

export function ProjectWall({
  slug,
  compact = false,
  ddollarBalance = null,
  summarizerEligible = false,
  shareOrigin,
}: ProjectWallProps) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [messages, setMessages] = useState<WallMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [membership, setMembership] = useState<WallMembership | null>(null);
  const [membershipLoading, setMembershipLoading] = useState(true);
  const [joining, setJoining] = useState(false);
  const [pinTarget, setPinTarget] = useState<WallMessage | null>(null);
  const [pinBusy, setPinBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [summary, setSummary] = useState<WallSummary | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevScrollHeightRef = useRef<number>(0);
  const origin = shareOrigin ?? (typeof window !== 'undefined' ? window.location.origin : 'https://doxxedcrypto.digital');

  const loadSummary = useCallback(async () => {
    try {
      const s = await fetchWallSummary(slug);
      setSummary(s);
      setSummaryError(null);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to load summary');
    }
  }, [slug]);

  const load = useCallback(async () => {
    try {
      const data = await fetchProjectWall(slug, token);
      setMessages(data);
      setHasMore(data.length > 0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load wall');
    } finally {
      setLoading(false);
    }
  }, [slug, token]);

  const loadMembership = useCallback(async () => {
    try {
      const m = await fetchWallMembership(slug, token);
      setMembership(m);
    } catch {
      setMembership(null);
    } finally {
      setMembershipLoading(false);
    }
  }, [slug, token]);

  useEffect(() => {
    setLoading(true);
    setMembershipLoading(true);
    void load();
    void loadMembership();
    void loadSummary();
    const id = setInterval(() => {
      void load();
      void loadSummary();
    }, 9000);
    return () => clearInterval(id);
  }, [load, loadMembership, loadSummary]);

  // Auto-scroll to bottom only when new messages arrive at the end (not when prepending older).
  useEffect(() => {
    if (scrollRef.current && !loadingOlder) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, loadingOlder]);

  const joined = membership?.joined ?? false;
  const isFounder = membership?.isFounder ?? false;

  async function loadOlder() {
    if (loadingOlder || !hasMore || messages.length === 0) return;
    const first = messages[0];
    setLoadingOlder(true);
    try {
      const older = await fetchProjectWall(slug, token, first.createdAt);
      if (older.length === 0) {
        setHasMore(false);
      } else {
        // Preserve scroll position after prepending.
        if (scrollRef.current) prevScrollHeightRef.current = scrollRef.current.scrollHeight;
        setMessages((prev) => [...older, ...prev]);
        requestAnimationFrame(() => {
          if (scrollRef.current) {
            const newHeight = scrollRef.current.scrollHeight;
            scrollRef.current.scrollTop = newHeight - prevScrollHeightRef.current;
          }
        });
        if (older.length < 100) setHasMore(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load older messages');
    } finally {
      setLoadingOlder(false);
    }
  }

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    if (el.scrollTop < 40 && hasMore && !loadingOlder) {
      void loadOlder();
    }
  }

  async function handleSend() {
    const body = draft.trim();
    if (!body || !token) return;
    setSending(true);
    setNotice(null);
    try {
      const msg = await postProjectWallMessage(slug, body, token);
      setMessages((prev) => [...prev, msg]);
      setDraft('');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to post';
      setError(msg);
    } finally {
      setSending(false);
    }
  }

  async function handleJoin() {
    if (!token) return;
    setNotice(null);
    setJoining(true);
    try {
      await joinProjectWall(slug, token);
      setMembership((prev) => (prev ? { ...prev, joined: true } : prev));
      setNotice('Joined — you can now post on this wall.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Failed to join');
    } finally {
      setJoining(false);
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

  async function handleActivateSummarizer() {
    if (!token) return;
    setSummaryBusy(true);
    setSummaryError(null);
    try {
      const s = await activateWallSummarizer(slug, token);
      setSummary(s);
      setNotice(`Chat Summarizer activated — ${WALL_SUMMARIZER_COST_DDOLLAR} DDollar/month.`);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to activate summarizer');
    } finally {
      setSummaryBusy(false);
    }
  }

  const canPin = Boolean(token) && joined;
  const showSummaryButton = summarizerEligible && Boolean(token);

  if (loading && messages.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/30 py-10 text-sm text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading wall…
      </div>
    );
  }

  // In compact drawer mode the host (founder-chat-launcher) already handles join gating,
  // so we always render the wall body there.
  const gateWall = !compact && !membershipLoading && !joined && Boolean(token);
  const signedOut = !token;

  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-[#0B0B0B] shadow-[0_0_40px_-12px_rgba(124,58,237,0.35)] ${
        compact ? 'max-h-[60vh]' : 'h-[70vh] min-h-[460px]'
      }`}
    >
      {/* Header strip — always visible (teaser) */}
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

      {/* Join guard — hide message body + composer for non-members */}
      {gateWall || signedOut ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-12 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full border border-violet-500/30 bg-violet-500/10">
            <Lock className="h-6 w-6 text-violet-300" />
          </div>
          {signedOut ? (
            <>
              <p className="text-sm font-semibold text-white">Sign in to join the conversation</p>
              <p className="max-w-sm text-xs text-zinc-500">
                This wall is members-only. Sign in, then join this project to read and post messages.
              </p>
              <Link
                href={`/login?callbackUrl=/project/${encodeURIComponent(slug)}`}
                className="mt-1 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500"
              >
                Sign in to join
              </Link>
            </>
          ) : (
            <>
              <p className="text-sm font-semibold text-white">Join to see chats</p>
              <p className="max-w-sm text-xs text-zinc-500">
                Follow this project to unlock the full message history and start posting on its community wall.
              </p>
              <button
                type="button"
                onClick={handleJoin}
                disabled={joining}
                className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:opacity-50"
              >
                {joining ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Join project
              </button>
            </>
          )}
        </div>
      ) : (
        <>
          {/* Summary card (above messages) */}
          {summary && summary.summaryBody && (
            <div className="border-b border-zinc-800 px-3 py-2.5">
              <WallSummaryCard
                slug={slug}
                projectName={messages[0]?.projectName ?? slug}
                ticker={messages[0]?.projectTicker ?? ''}
                summary={summary}
                origin={origin}
                onRenew={handleActivateSummarizer}
                renewing={summaryBusy}
              />
            </div>
          )}

          {/* Messages */}
          <div ref={scrollRef} onScroll={handleScroll} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {hasMore && messages.length > 0 && (
              <div className="flex justify-center pb-1">
                <button
                  type="button"
                  onClick={loadOlder}
                  disabled={loadingOlder}
                  className="inline-flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900/70 px-3 py-1 text-[10px] text-zinc-400 transition hover:border-violet-500/50 hover:text-violet-200 disabled:opacity-50"
                >
                  {loadingOlder ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                  Load older messages
                </button>
              </div>
            )}
            {!hasMore && messages.length > 0 && (
              <p className="text-center text-[10px] text-zinc-700">— beginning of chat —</p>
            )}
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
                mine={session?.user?.id === m.authorId}
                canPin={canPin}
                onPin={setPinTarget}
              />
            ))}
          </div>

          {/* Composer */}
          <div className="border-t border-zinc-800 bg-zinc-950/60 px-3 py-2.5">
            {notice && <p className="mb-1.5 text-[11px] text-emerald-400">{notice}</p>}
            {summaryError && (
              <p className="mb-1.5 text-[11px] text-red-400">Summarizer: {summaryError}</p>
            )}

            {/* Summarizer agent button — immediately above Send, only on qualifying projects */}
            {showSummaryButton && (
              <div className="mb-2 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={handleActivateSummarizer}
                  disabled={summaryBusy}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-violet-500/40 bg-violet-600/15 px-2.5 py-1.5 text-[11px] font-semibold text-violet-100 transition hover:bg-violet-600/25 disabled:opacity-50"
                  title="Activate the Chat Summarizer agent — 1,000 DDollar/month"
                >
                  {summaryBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  {summary?.active ? 'Refresh summary' : 'Activate agent'}
                  <span className="text-violet-300/70">· {WALL_SUMMARIZER_COST_DDOLLAR} DD/mo</span>
                </button>
              </div>
            )}

            {/* Founder rich-posting affordances — verified project founder only (chat-only for everyone else). */}
            {isFounder && (
              <div className="mb-2 flex items-center gap-1.5 text-[10px] text-zinc-500">
                <span className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900/60 px-1.5 py-1 opacity-60" title="Coming soon — founder-only rich posts">
                  <Video className="h-3 w-3" /> Founder video
                </span>
                <span className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900/60 px-1.5 py-1 opacity-60" title="Coming soon — founder-only milestone posts">
                  <Milestone className="h-3 w-3" /> Raise milestone
                </span>
                <span className="text-zinc-600">· founder-only rich posts (soon)</span>
              </div>
            )}

            {!token ? (
              <Link
                href={`/login?callbackUrl=/project/${encodeURIComponent(slug)}`}
                className="block w-full rounded-lg border border-zinc-700 px-3 py-2 text-center text-xs text-zinc-300 transition hover:border-zinc-600 hover:text-white"
              >
                Sign in to join the conversation
              </Link>
            ) : !joined ? (
              <button
                type="button"
                onClick={handleJoin}
                disabled={joining}
                className="w-full rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-violet-500 disabled:opacity-50"
              >
                {joining ? 'Joining…' : 'Join project to post'}
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
        </>
      )}

      <PinConfirmModal
        message={pinTarget}
        balance={ddollarBalance}
        onClose={() => !pinBusy && setPinTarget(null)}
        onConfirm={handlePin}
      />
    </div>
  );
}

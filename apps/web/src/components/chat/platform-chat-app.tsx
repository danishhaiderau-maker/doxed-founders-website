'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  Check,
  CheckCheck,
  Hash,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  Search,
  Send,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { formatRelativeTime } from '@dcf/utils';
import {
  fetchAggregatedWall,
  fetchMessageConversation,
  fetchMessageThreads,
  fetchMyWallGroups,
  fetchMyWallUnread,
  fetchProjectWall,
  joinProjectWall,
  markWallRead,
  postProjectWallMessage,
  resolveMessageRecipient,
  sendPlatformMessage,
  type MessageThread,
  type PlatformMessageItem,
  type WallGroupEntry,
  type WallMessage,
  type WallUnreadEntry,
} from '@/lib/api';
import { dispatchInboxRefresh } from '@/lib/inbox-refresh';
import { SiteBrand } from '@/components/site-nav';

type ChatTab = 'direct' | 'groups';

type ActiveChat =
  | { kind: 'dm'; userId: string; label: string }
  | { kind: 'group'; slug: string; name: string; ticker: string }
  | { kind: 'all' }
  | null;

function fmtClock(ts: string): string {
  const d = new Date(ts);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `${hh}:${mm}`;
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${hh}:${mm}`;
}

function dayLabel(ts: string): string {
  const d = new Date(ts);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startMsg = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayMs = 86_400_000;
  if (startMsg === startToday) return 'Today';
  if (startMsg === startToday - dayMs) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function DayDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-2">
      <span className="rounded-lg bg-[#182229] px-3 py-1 text-[11px] font-medium text-zinc-400 shadow-sm">
        {label}
      </span>
    </div>
  );
}

function initials(label: string): string {
  const parts = label.replace(/^@/, '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] ?? ''}${parts[1][0] ?? ''}`.toUpperCase();
}

function Avatar({
  label,
  logoUrl,
  tone = 'zinc',
}: {
  label: string;
  logoUrl?: string | null;
  tone?: 'zinc' | 'emerald' | 'cyan';
}) {
  const toneClass =
    tone === 'emerald'
      ? 'bg-emerald-500/15 text-emerald-300'
      : tone === 'cyan'
        ? 'bg-cyan-500/15 text-cyan-300'
        : 'bg-zinc-800 text-zinc-300';
  return (
    <div className={`flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-xs font-bold ${toneClass}`}>
      {logoUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={logoUrl} alt="" className="h-full w-full object-cover" />
      ) : (
        initials(label)
      )}
    </div>
  );
}

export function PlatformChatApp() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const myId = session?.user?.id;
  const router = useRouter();
  const searchParams = useSearchParams();

  const [tab, setTab] = useState<ChatTab>('direct');
  const [threads, setThreads] = useState<MessageThread[]>([]);
  const [groups, setGroups] = useState<WallGroupEntry[]>([]);
  const [wallUnread, setWallUnread] = useState<WallUnreadEntry[]>([]);
  const [active, setActive] = useState<ActiveChat>(null);
  const [dmMessages, setDmMessages] = useState<PlatformMessageItem[]>([]);
  const [groupMessages, setGroupMessages] = useState<WallMessage[]>([]);
  const [aggregated, setAggregated] = useState<WallMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingThread, setLoadingThread] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsJoin, setNeedsJoin] = useState(false);
  const [joining, setJoining] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchHit, setSearchHit] = useState<{
    userId: string;
    label: string;
    platformHandle: string | null;
  } | null>(null);
  const [mobileShowThread, setMobileShowThread] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const deepLinkApplied = useRef(false);

  const loadLists = useCallback(async () => {
    if (!token) return;
    setLoadingList(true);
    try {
      const [t, g, u] = await Promise.all([
        fetchMessageThreads(token),
        fetchMyWallGroups(token).catch(() => [] as WallGroupEntry[]),
        fetchMyWallUnread(token).catch(() => ({ total: 0, projects: [] as WallUnreadEntry[] })),
      ]);
      setThreads(t);
      setGroups(g);
      setWallUnread(u.projects);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load chats');
    } finally {
      setLoadingList(false);
    }
  }, [token]);

  const loadDm = useCallback(
    async (otherUserId: string) => {
      if (!token) return;
      setLoadingThread(true);
      try {
        const data = await fetchMessageConversation(otherUserId, token);
        setDmMessages(data);
        setError(null);
        await loadLists();
        dispatchInboxRefresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load conversation');
        setDmMessages([]);
      } finally {
        setLoadingThread(false);
      }
    },
    [token, loadLists],
  );

  const loadGroup = useCallback(
    async (slug: string) => {
      if (!token) return;
      setLoadingThread(true);
      try {
        const data = await fetchProjectWall(slug, token);
        setGroupMessages(data);
        setNeedsJoin(false);
        setError(null);
        void markWallRead(slug, token).catch(() => {});
        setWallUnread((prev) => prev.filter((u) => u.slug !== slug));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load group');
        setGroupMessages([]);
      } finally {
        setLoadingThread(false);
      }
    },
    [token],
  );

  const loadAllGroups = useCallback(async () => {
    if (!token) return;
    setLoadingThread(true);
    try {
      const data = await fetchAggregatedWall(token, 100);
      setAggregated(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load feed');
      setAggregated([]);
    } finally {
      setLoadingThread(false);
    }
  }, [token]);

  useEffect(() => {
    if (token) void loadLists();
  }, [token, loadLists]);

  // Deep links: ?dm=<userId>&label=…  or  ?group=<slug>
  useEffect(() => {
    if (!token || deepLinkApplied.current) return;
    const dm = searchParams.get('dm');
    const group = searchParams.get('group');
    const label = searchParams.get('label');
    if (dm) {
      deepLinkApplied.current = true;
      setTab('direct');
      setActive({ kind: 'dm', userId: dm, label: label || 'Conversation' });
      setMobileShowThread(true);
      return;
    }
    if (group === 'all') {
      deepLinkApplied.current = true;
      setTab('groups');
      setActive({ kind: 'all' });
      setMobileShowThread(true);
      return;
    }
    if (group) {
      deepLinkApplied.current = true;
      setTab('groups');
      const g = groups.find((x) => x.project.slug === group);
      setActive({
        kind: 'group',
        slug: group,
        name: g?.project.name ?? group,
        ticker: g?.project.ticker ?? '',
      });
      setMobileShowThread(true);
    }
  }, [token, searchParams, groups]);

  useEffect(() => {
    if (!active) {
      setDmMessages([]);
      setGroupMessages([]);
      setAggregated([]);
      setDraft('');
      setNeedsJoin(false);
      return;
    }
    setDraft('');
    setNeedsJoin(false);
    if (active.kind === 'dm') void loadDm(active.userId);
    else if (active.kind === 'group') void loadGroup(active.slug);
    else if (active.kind === 'all') void loadAllGroups();
  }, [active, loadDm, loadGroup, loadAllGroups]);

  // Poll active thread
  useEffect(() => {
    if (!token || !active) return;
    const id = setInterval(() => {
      if (active.kind === 'dm') void loadDm(active.userId);
      else if (active.kind === 'group') void loadGroup(active.slug);
      else if (active.kind === 'all') void loadAllGroups();
      void loadLists();
    }, 10_000);
    return () => clearInterval(id);
  }, [token, active, loadDm, loadGroup, loadAllGroups, loadLists]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [dmMessages.length, groupMessages.length, aggregated.length, active]);

  // Sync URL when selecting a chat
  const selectActive = useCallback(
    (next: ActiveChat) => {
      setActive(next);
      setMobileShowThread(Boolean(next));
      setSearchHit(null);
      if (!next) {
        router.replace('/chat', { scroll: false });
        return;
      }
      if (next.kind === 'dm') {
        const q = new URLSearchParams({ dm: next.userId });
        if (next.label) q.set('label', next.label);
        router.replace(`/chat?${q.toString()}`, { scroll: false });
      } else if (next.kind === 'group') {
        router.replace(`/chat?group=${encodeURIComponent(next.slug)}`, { scroll: false });
      } else {
        router.replace('/chat?group=all', { scroll: false });
      }
    },
    [router],
  );

  // Keep DM header label in sync once threads load (deep links often only have userId).
  useEffect(() => {
    if (!active || active.kind !== 'dm') return;
    const match = threads.find((t) => t.otherUserId === active.userId);
    if (match && match.otherUserLabel && match.otherUserLabel !== active.label) {
      setActive({ kind: 'dm', userId: active.userId, label: match.otherUserLabel });
    }
  }, [threads, active]);

  const filteredThreads = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return threads.filter((t) => {
      if (unreadOnly && t.unreadCount <= 0) return false;
      if (!q) return true;
      return (
        t.otherUserLabel.toLowerCase().includes(q) ||
        t.lastBody.toLowerCase().includes(q)
      );
    });
  }, [threads, searchQuery, unreadOnly]);

  const filteredGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return groups.filter((g) => {
      const unread = wallUnread.find((u) => u.slug === g.project.slug)?.unreadCount ?? 0;
      if (unreadOnly && unread <= 0) return false;
      if (!q) return true;
      return (
        g.project.name.toLowerCase().includes(q) ||
        g.project.ticker.toLowerCase().includes(q) ||
        (g.project.founder?.name ?? '').toLowerCase().includes(q)
      );
    });
  }, [groups, searchQuery, unreadOnly, wallUnread]);

  async function handleSearchUser(e?: React.FormEvent) {
    e?.preventDefault();
    if (!token || !searchQuery.trim()) return;
    setSearchBusy(true);
    setError(null);
    setSearchHit(null);
    try {
      const res = await resolveMessageRecipient(searchQuery.trim(), token);
      setSearchHit({
        userId: res.userId,
        label: res.label,
        platformHandle: res.platformHandle,
      });
      setTab('direct');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No user found');
    } finally {
      setSearchBusy(false);
    }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !active || !draft.trim()) return;
    const body = draft.trim();
    setBusy(true);
    setError(null);
    try {
      if (active.kind === 'dm') {
        await sendPlatformMessage(active.userId, body, token);
        setDraft('');
        await loadDm(active.userId);
      } else if (active.kind === 'group') {
        try {
          const msg = await postProjectWallMessage(active.slug, body, token);
          setGroupMessages((prev) => [...prev, msg]);
          setDraft('');
          setNeedsJoin(false);
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to post';
          if (/join|follower|forbidden|403/i.test(message)) {
            setNeedsJoin(true);
          }
          throw err;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Send failed');
    } finally {
      setBusy(false);
      composerRef.current?.focus();
    }
  }

  async function joinAndRetry() {
    if (!token || !active || active.kind !== 'group') return;
    setJoining(true);
    setError(null);
    try {
      await joinProjectWall(active.slug, token);
      setNeedsJoin(false);
      await loadLists();
      if (draft.trim()) {
        const msg = await postProjectWallMessage(active.slug, draft.trim(), token);
        setGroupMessages((prev) => [...prev, msg]);
        setDraft('');
      } else {
        await loadGroup(active.slug);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to join');
    } finally {
      setJoining(false);
    }
  }

  const wallUnreadFor = (slug: string) =>
    wallUnread.find((u) => u.slug === slug)?.unreadCount ?? 0;

  const headerTitle = useMemo(() => {
    if (!active) return null;
    if (active.kind === 'dm') return active.label;
    if (active.kind === 'group') return active.name;
    return 'All groups';
  }, [active]);

  const headerSub = useMemo(() => {
    if (!active) return null;
    if (active.kind === 'dm') return 'Direct message';
    if (active.kind === 'group') return active.ticker ? `#${active.ticker}` : 'Project wall';
    return 'Unified feed across your project groups';
  }, [active]);

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#0b141a] text-zinc-400">
        <Loader2 className="h-6 w-6 animate-spin text-emerald-400" />
        <p className="text-sm">Opening chat…</p>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="flex min-h-screen flex-col bg-[#0b141a]">
        <header className="flex items-center justify-between border-b border-white/5 bg-[#111b21] px-4 py-3">
          <SiteBrand className="text-sm" />
          <Link href="/" className="text-xs text-zinc-400 hover:text-white">
            Home
          </Link>
        </header>
        <div className="flex flex-1 flex-col items-center justify-center gap-5 px-6 text-center">
          <div className="flex h-24 w-24 items-center justify-center rounded-full border border-emerald-500/25 bg-emerald-500/10">
            <MessageSquare className="h-11 w-11 text-emerald-400" />
          </div>
          <div className="max-w-md space-y-2">
            <h1 className="text-2xl font-semibold text-white">Doxxed Crypto Chat</h1>
            <p className="text-sm leading-relaxed text-zinc-400">
              WhatsApp-style messaging for the platform — DM any admin, founder, or trader, and join
              project group walls.
            </p>
          </div>
          <ul className="max-w-sm space-y-2 text-left text-sm text-zinc-400">
            <li className="flex gap-2">
              <Search className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              Search by @handle, admin name, or messaging address
            </li>
            <li className="flex gap-2">
              <Users className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              Project groups + direct messages in one inbox
            </li>
            <li className="flex gap-2">
              <Send className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              Full-page thread view — not a tiny popover
            </li>
          </ul>
          <Link
            href="/login?callbackUrl=/chat"
            className="rounded-xl bg-emerald-600 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-900/40 hover:bg-emerald-500"
          >
            Sign in to start chatting
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] flex-col bg-[#0b141a] text-zinc-100">
      {/* Top chrome */}
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/5 bg-[#111b21] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <SiteBrand className="text-sm" />
          <span className="hidden h-4 w-px bg-zinc-700 sm:block" />
          <h1 className="truncate text-sm font-semibold text-white">Chat</h1>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/projects"
            className="hidden rounded-lg px-2.5 py-1.5 text-xs text-zinc-400 transition hover:bg-white/5 hover:text-white sm:inline"
          >
            Browse projects
          </Link>
          <Link
            href="/"
            className="rounded-lg px-2.5 py-1.5 text-xs text-zinc-400 transition hover:bg-white/5 hover:text-white"
          >
            Home
          </Link>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left rail — conversation list */}
        <aside
          className={`flex w-full shrink-0 flex-col border-r border-white/5 bg-[#111b21] md:w-[380px] lg:w-[420px] ${
            mobileShowThread ? 'hidden md:flex' : 'flex'
          }`}
        >
          <div className="border-b border-white/5 px-3 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-white">Chats</p>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setUnreadOnly((v) => !v)}
                  className={`rounded-lg px-2 py-1 text-[11px] font-semibold transition ${
                    unreadOnly
                      ? 'bg-emerald-600/30 text-emerald-200'
                      : 'text-zinc-500 hover:bg-white/5 hover:text-zinc-300'
                  }`}
                  title="Show unread only"
                >
                  Unread
                </button>
                <button
                  type="button"
                  onClick={() => {
                    searchInputRef.current?.focus();
                    searchInputRef.current?.select();
                  }}
                  className="inline-flex items-center gap-1 rounded-lg bg-emerald-600/20 px-2 py-1 text-[11px] font-semibold text-emerald-300 transition hover:bg-emerald-600/30"
                  title="Start a new chat"
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                  New
                </button>
              </div>
            </div>
            <form onSubmit={handleSearchUser} className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
              <input
                ref={searchInputRef}
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setSearchHit(null);
                }}
                placeholder="Search chats or find @admin / handle…"
                className="w-full rounded-lg border border-transparent bg-[#202c33] py-2.5 pl-9 pr-20 text-sm text-white placeholder:text-zinc-500 focus:border-emerald-500/40 focus:outline-none"
              />
              <button
                type="submit"
                disabled={searchBusy || !searchQuery.trim()}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md bg-emerald-600/90 px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-40"
              >
                {searchBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Find'}
              </button>
            </form>
            <p className="mt-1.5 text-[10px] text-zinc-500">
              Find anyone by @handle, messaging address, admin name, or user ID — then message them directly.
            </p>
          </div>

          {searchHit && (
            <button
              type="button"
              onClick={() =>
                selectActive({ kind: 'dm', userId: searchHit.userId, label: searchHit.label })
              }
              className="mx-3 mt-3 flex items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2.5 text-left transition hover:bg-emerald-500/15"
            >
              <Avatar label={searchHit.label} tone="emerald" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-white">{searchHit.label}</p>
                <p className="truncate text-[11px] text-emerald-200/80">
                  {searchHit.platformHandle ? `@${searchHit.platformHandle}` : 'Start a direct message'}
                </p>
              </div>
              <Send className="h-4 w-4 shrink-0 text-emerald-300" />
            </button>
          )}

          <div className="mt-2 flex gap-1 px-3">
            <button
              type="button"
              onClick={() => setTab('direct')}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition ${
                tab === 'direct'
                  ? 'bg-[#202c33] text-emerald-300'
                  : 'text-zinc-500 hover:bg-white/5 hover:text-zinc-300'
              }`}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              Direct
              {threads.some((t) => t.unreadCount > 0) && (
                <span className="rounded-full bg-emerald-500 px-1.5 text-[9px] text-black">
                  {threads.reduce((n, t) => n + t.unreadCount, 0)}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => setTab('groups')}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition ${
                tab === 'groups'
                  ? 'bg-[#202c33] text-emerald-300'
                  : 'text-zinc-500 hover:bg-white/5 hover:text-zinc-300'
              }`}
            >
              <Users className="h-3.5 w-3.5" />
              Groups
              {wallUnread.reduce((n, u) => n + u.unreadCount, 0) > 0 && (
                <span className="rounded-full bg-emerald-500 px-1.5 text-[9px] text-black">
                  {wallUnread.reduce((n, u) => n + u.unreadCount, 0)}
                </span>
              )}
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
            {loadingList && threads.length === 0 && groups.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-16 text-xs text-zinc-500">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading chats…
              </div>
            ) : tab === 'direct' ? (
              filteredThreads.length === 0 ? (
                <div className="px-4 py-12 text-center">
                  <p className="text-sm text-zinc-400">No direct messages yet.</p>
                  <p className="mt-1 text-xs text-zinc-600">
                    Search an admin or trader above to start chatting.
                  </p>
                </div>
              ) : (
                filteredThreads.map((t) => {
                  const selected = active?.kind === 'dm' && active.userId === t.otherUserId;
                  return (
                    <button
                      key={t.otherUserId}
                      type="button"
                      onClick={() =>
                        selectActive({
                          kind: 'dm',
                          userId: t.otherUserId,
                          label: t.otherUserLabel,
                        })
                      }
                      className={`mb-0.5 flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition ${
                        selected ? 'bg-[#2a3942]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Avatar label={t.otherUserLabel} tone="cyan" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p
                            className={`truncate text-sm ${
                              t.unreadCount > 0 ? 'font-semibold text-white' : 'font-medium text-zinc-200'
                            }`}
                          >
                            {t.otherUserLabel}
                          </p>
                          <span className="shrink-0 text-[10px] text-zinc-500">
                            {formatRelativeTime(t.lastAt)}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center justify-between gap-2">
                          <p className="truncate text-[12px] text-zinc-500">{t.lastBody}</p>
                          {t.unreadCount > 0 && (
                            <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-black">
                              {t.unreadCount > 9 ? '9+' : t.unreadCount}
                            </span>
                          )}
                        </div>
                      </div>
                    </button>
                  );
                })
              )
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => selectActive({ kind: 'all' })}
                  className={`mb-0.5 flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition ${
                    active?.kind === 'all' ? 'bg-[#2a3942]' : 'hover:bg-white/[0.04]'
                  }`}
                >
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300">
                    <Hash className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-zinc-200">All groups</p>
                    <p className="truncate text-[12px] text-zinc-500">Unified wall feed</p>
                  </div>
                </button>
                {filteredGroups.length === 0 ? (
                  <div className="px-4 py-10 text-center">
                    <p className="text-sm text-zinc-400">No project groups yet.</p>
                    <Link href="/projects" className="mt-2 inline-block text-xs text-emerald-400 hover:underline">
                      Browse projects to join →
                    </Link>
                  </div>
                ) : (
                  filteredGroups.map((g) => {
                    const unread = wallUnreadFor(g.project.slug);
                    const selected = active?.kind === 'group' && active.slug === g.project.slug;
                    return (
                      <button
                        key={g.project.id}
                        type="button"
                        onClick={() =>
                          selectActive({
                            kind: 'group',
                            slug: g.project.slug,
                            name: g.project.name,
                            ticker: g.project.ticker,
                          })
                        }
                        className={`mb-0.5 flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition ${
                          selected ? 'bg-[#2a3942]' : 'hover:bg-white/[0.04]'
                        }`}
                      >
                        <Avatar
                          label={g.project.ticker}
                          logoUrl={g.project.logoUrl}
                          tone="emerald"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <p
                              className={`truncate text-sm ${
                                unread > 0 ? 'font-semibold text-white' : 'font-medium text-zinc-200'
                              }`}
                            >
                              {g.project.name}
                            </p>
                            <span className="shrink-0 text-[10px] text-zinc-500">
                              {g.lastMessage ? fmtClock(g.lastMessage.createdAt) : ''}
                            </span>
                          </div>
                          <div className="mt-0.5 flex items-center justify-between gap-2">
                            <p className="truncate text-[12px] text-zinc-500">
                              {g.lastMessage?.body ?? `${g.project.ticker} · join the conversation`}
                            </p>
                            {unread > 0 && (
                              <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-black">
                                {unread > 9 ? '9+' : unread}
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })
                )}
              </>
            )}
          </div>
        </aside>

        {/* Right pane — active conversation */}
        <section
          className={`relative flex min-w-0 flex-1 flex-col ${
            mobileShowThread ? 'flex' : 'hidden md:flex'
          }`}
          style={{
            backgroundImage:
              'radial-gradient(ellipse at top, rgba(16,185,129,0.06), transparent 55%), linear-gradient(180deg, #0b141a 0%, #0a1014 100%)',
          }}
        >
          {!active ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="flex h-20 w-20 items-center justify-center rounded-full border border-emerald-500/20 bg-emerald-500/10">
                <MessageSquare className="h-9 w-9 text-emerald-400/90" />
              </div>
              <h2 className="text-xl font-semibold text-white">Doxxed Crypto Chat</h2>
              <p className="max-w-sm text-sm text-zinc-400">
                Pick a conversation on the left, or search for an admin / founder / trader to message them directly.
              </p>
            </div>
          ) : (
            <>
              <div className="flex shrink-0 items-center gap-3 border-b border-white/5 bg-[#202c33]/90 px-3 py-2.5 backdrop-blur">
                <button
                  type="button"
                  className="rounded-lg p-1.5 text-zinc-400 hover:bg-white/5 hover:text-white md:hidden"
                  onClick={() => {
                    setMobileShowThread(false);
                    selectActive(null);
                  }}
                  aria-label="Back to chats"
                >
                  <X className="h-5 w-5" />
                </button>
                <Avatar
                  label={headerTitle ?? '?'}
                  logoUrl={
                    active.kind === 'group'
                      ? groups.find((g) => g.project.slug === active.slug)?.project.logoUrl
                      : null
                  }
                  tone={active.kind === 'dm' ? 'cyan' : 'emerald'}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-white">{headerTitle}</p>
                  <p className="truncate text-[11px] text-zinc-400">{headerSub}</p>
                </div>
                {active.kind === 'group' && (
                  <Link
                    href={`/project/${active.slug}`}
                    className="shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-emerald-300 hover:bg-white/5"
                  >
                    Open project
                  </Link>
                )}
              </div>

              <div ref={scrollRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-4 sm:px-6">
                {error && (
                  <p className="mx-auto max-w-lg rounded-lg border border-red-500/30 bg-red-950/40 px-3 py-2 text-center text-xs text-red-200">
                    {error}
                  </p>
                )}
                {loadingThread &&
                  dmMessages.length === 0 &&
                  groupMessages.length === 0 &&
                  aggregated.length === 0 && (
                    <div className="flex items-center justify-center gap-2 py-20 text-xs text-zinc-500">
                      <Loader2 className="h-4 w-4 animate-spin" /> Loading messages…
                    </div>
                  )}

                {active.kind === 'dm' &&
                  dmMessages.map((m, i) => {
                    const prev = dmMessages[i - 1];
                    const showDay = !prev || dayLabel(prev.createdAt) !== dayLabel(m.createdAt);
                    return (
                      <div key={m.id}>
                        {showDay && <DayDivider label={dayLabel(m.createdAt)} />}
                        <div className={`flex ${m.mine ? 'justify-end' : 'justify-start'}`}>
                          <div
                            className={`max-w-[min(85%,28rem)] rounded-2xl px-3 py-2 text-sm shadow-sm ${
                              m.mine
                                ? 'rounded-br-md bg-[#005c4b] text-emerald-50'
                                : 'rounded-bl-md bg-[#202c33] text-zinc-100'
                            }`}
                          >
                            {!m.mine && (
                              <p className="mb-0.5 text-[10px] font-semibold text-cyan-300/90">{m.fromLabel}</p>
                            )}
                            <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                            <p
                              className={`mt-1 flex items-center justify-end gap-1 text-[10px] ${
                                m.mine ? 'text-emerald-200/60' : 'text-zinc-500'
                              }`}
                            >
                              {fmtClock(m.createdAt)}
                              {m.mine &&
                                (m.readAt ? (
                                  <CheckCheck className="h-3.5 w-3.5 text-sky-300" aria-label="Read" />
                                ) : (
                                  <Check className="h-3.5 w-3.5" aria-label="Sent" />
                                ))}
                            </p>
                          </div>
                        </div>
                      </div>
                    );
                  })}

                {active.kind === 'group' &&
                  groupMessages.map((m, i) => {
                    const mine = myId === m.authorId;
                    const prev = groupMessages[i - 1];
                    const showDay = !prev || dayLabel(prev.createdAt) !== dayLabel(m.createdAt);
                    return (
                      <div key={m.id}>
                        {showDay && <DayDivider label={dayLabel(m.createdAt)} />}
                        <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                          <div
                            className={`max-w-[min(85%,28rem)] rounded-2xl px-3 py-2 text-sm shadow-sm ${
                              mine
                                ? 'rounded-br-md bg-[#005c4b] text-emerald-50'
                                : 'rounded-bl-md bg-[#202c33] text-zinc-100'
                            }`}
                          >
                            {!mine && (
                              <div className="mb-0.5 flex flex-wrap items-center gap-1 text-[10px]">
                                <span className="font-semibold text-emerald-300">
                                  {m.author.name ?? m.author.platformHandle ?? 'Anon'}
                                </span>
                                {m.author.isVerifiedFounder && (
                                  <span className="inline-flex items-center gap-0.5 text-emerald-400">
                                    <ShieldCheck className="h-2.5 w-2.5" /> founder
                                  </span>
                                )}
                              </div>
                            )}
                            <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                            <p className={`mt-1 text-right text-[10px] ${mine ? 'text-emerald-200/60' : 'text-zinc-500'}`}>
                              {fmtClock(m.createdAt)}
                            </p>
                          </div>
                        </div>
                      </div>
                    );
                  })}

                {active.kind === 'all' &&
                  (aggregated.length === 0 && !loadingThread ? (
                    <div className="py-16 text-center text-sm text-zinc-500">
                      No messages across your groups yet.
                    </div>
                  ) : (
                    aggregated.map((m, i) => {
                      const mine = myId === m.authorId;
                      const prev = aggregated[i - 1];
                      const showDay = !prev || dayLabel(prev.createdAt) !== dayLabel(m.createdAt);
                      return (
                        <div key={m.id}>
                          {showDay && <DayDivider label={dayLabel(m.createdAt)} />}
                          <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                            <div
                              className={`max-w-[min(85%,28rem)] rounded-2xl px-3 py-2 text-sm shadow-sm ${
                                mine
                                  ? 'rounded-br-md bg-[#005c4b] text-emerald-50'
                                  : 'rounded-bl-md bg-[#202c33] text-zinc-100'
                              }`}
                            >
                              <div className="mb-0.5 flex flex-wrap items-center gap-1 text-[10px]">
                                <Link
                                  href={`/project/${m.projectSlug}`}
                                  className="font-semibold text-emerald-300 hover:underline"
                                >
                                  {m.projectName}
                                </Link>
                                <span className="text-zinc-600">·</span>
                                <span className="text-zinc-400">
                                  {m.author.name ?? m.author.platformHandle ?? 'Anon'}
                                </span>
                              </div>
                              <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                              <p className="mt-1 text-right text-[10px] text-zinc-500">{fmtClock(m.createdAt)}</p>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ))}

                {active.kind === 'dm' && !loadingThread && dmMessages.length === 0 && (
                  <div className="py-16 text-center text-sm text-zinc-500">
                    No messages yet — say hello.
                  </div>
                )}
                {active.kind === 'group' && !loadingThread && groupMessages.length === 0 && (
                  <div className="py-16 text-center text-sm text-zinc-500">
                    No messages on this wall yet.
                  </div>
                )}
              </div>

              {active.kind === 'all' ? (
                <div className="shrink-0 border-t border-white/5 bg-[#111b21] px-4 py-3 text-center text-[11px] text-zinc-500">
                  Pick a project group on the left to post a message.
                </div>
              ) : needsJoin ? (
                <div className="shrink-0 border-t border-white/5 bg-[#111b21] px-4 py-3">
                  <div className="mx-auto flex max-w-xl flex-col items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3 text-center">
                    <p className="text-xs text-zinc-300">Join this project to post on its wall.</p>
                    <button
                      type="button"
                      onClick={() => void joinAndRetry()}
                      disabled={joining}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                    >
                      {joining ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Users className="h-3.5 w-3.5" />}
                      Join &amp; post
                    </button>
                  </div>
                </div>
              ) : (
                <form
                  onSubmit={handleSend}
                  className="shrink-0 border-t border-white/5 bg-[#111b21] px-3 py-2.5 sm:px-4"
                >
                  <div className="mx-auto flex max-w-3xl items-end gap-2">
                    <textarea
                      ref={composerRef}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          void handleSend(e);
                        }
                      }}
                      rows={1}
                      maxLength={active.kind === 'dm' ? 4000 : 500}
                      placeholder={
                        active.kind === 'dm'
                          ? `Message ${active.label}…`
                          : `Message ${active.kind === 'group' ? active.name : ''}…`
                      }
                      className="max-h-32 min-h-[44px] flex-1 resize-none rounded-xl border border-transparent bg-[#202c33] px-3.5 py-3 text-sm text-white placeholder:text-zinc-500 focus:border-emerald-500/30 focus:outline-none"
                    />
                    <button
                      type="submit"
                      disabled={busy || !draft.trim()}
                      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Send message"
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

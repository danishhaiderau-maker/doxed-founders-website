'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  Archive,
  BellOff,
  Check,
  CheckCheck,
  Hash,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  Pin,
  Reply,
  Search,
  Send,
  Shield,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { formatRelativeTime } from '@dcf/utils';
import {
  CHAT_REACTION_EMOJIS,
  chatEventsStreamUrl,
  chatPresenceHeartbeat,
  fetchAggregatedWall,
  fetchMessageConversation,
  fetchMessageThreads,
  fetchMyWallGroups,
  fetchMyWallUnread,
  fetchProjectWall,
  fetchWallMembership,
  joinProjectWall,
  markAllMessagesRead,
  markAllWallsRead,
  markWallRead,
  postProjectWallMessage,
  reactToDmMessage,
  reactToWallMessage,
  reportWallMessage,
  resolveMessageRecipient,
  sendPlatformMessage,
  setChatThreadPref,
  updateWallSettings,
  type MessageThread,
  type PlatformMessageItem,
  type WallGroupEntry,
  type WallMembership,
  type WallMessage,
  type WallUnreadEntry,
} from '@/lib/api';
import { dispatchInboxRefresh } from '@/lib/inbox-refresh';
import { SiteBrand } from '@/components/site-nav';

type ChatTab = 'direct' | 'groups';

type ActiveChat =
  | { kind: 'dm'; userId: string; label: string }
  | { kind: 'group'; slug: string; name: string; ticker: string; projectId?: string }
  | { kind: 'all' }
  | null;

type ReplyTarget = { id: string; preview: string; authorLabel: string };

type ConversationPeer = {
  userId: string;
  lastSeenAt: string | null;
  online: boolean;
  isAdmin: boolean;
  isVerifiedFounder: boolean;
  founderSlug: string | null;
};

const REACTIONS = CHAT_REACTION_EMOJIS;

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
  online,
}: {
  label: string;
  logoUrl?: string | null;
  tone?: 'zinc' | 'emerald' | 'cyan';
  online?: boolean;
}) {
  const toneClass =
    tone === 'emerald'
      ? 'bg-emerald-500/15 text-emerald-300'
      : tone === 'cyan'
        ? 'bg-cyan-500/15 text-cyan-300'
        : 'bg-zinc-800 text-zinc-300';
  return (
    <div className="relative shrink-0">
      <div
        className={`flex h-11 w-11 items-center justify-center overflow-hidden rounded-full text-xs font-bold ${toneClass}`}
      >
        {logoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logoUrl} alt="" className="h-full w-full object-cover" />
        ) : (
          initials(label)
        )}
      </div>
      {online ? (
        <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-[#111b21] bg-emerald-400" />
      ) : null}
    </div>
  );
}

function BadgeRow({
  isAdmin,
  isVerifiedFounder,
}: {
  isAdmin?: boolean;
  isVerifiedFounder?: boolean;
}) {
  if (!isAdmin && !isVerifiedFounder) return null;
  return (
    <span className="inline-flex items-center gap-1">
      {isAdmin ? (
        <span className="inline-flex items-center gap-0.5 rounded bg-sky-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide text-sky-300">
          <Shield className="h-2.5 w-2.5" /> Admin
        </span>
      ) : null}
      {isVerifiedFounder ? (
        <span className="inline-flex items-center gap-0.5 rounded bg-emerald-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide text-emerald-300">
          <ShieldCheck className="h-2.5 w-2.5" /> Verified
        </span>
      ) : null}
    </span>
  );
}

/** In-platform link chips only (project / agent-hub / raise-room / founder). */
function PlatformLinkPreviews({ text }: { text: string }) {
  const links = useMemo(() => {
    const found: { href: string; label: string }[] = [];
    const re =
      /(?:https?:\/\/(?:www\.)?doxxedcrypto\.digital)?(\/(?:project|agent-hub|raise-room|founder)\/[^\s?#]+|\/raise-room(?:\?[^\s]+)?)/gi;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const path = m[1]!;
      let label = path;
      if (path.startsWith('/project/')) label = `Project · ${path.split('/')[2]}`;
      else if (path.startsWith('/agent-hub')) label = `Agent hub · ${path.split('/')[2] ?? 'home'}`;
      else if (path.startsWith('/raise-room')) label = 'Raise room';
      else if (path.startsWith('/founder/')) label = `Founder · ${path.split('/')[2]}`;
      if (!found.some((f) => f.href === path)) found.push({ href: path, label });
    }
    return found.slice(0, 3);
  }, [text]);

  if (links.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      {links.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          className="rounded-lg border border-emerald-500/20 bg-black/20 px-2.5 py-1.5 text-[11px] font-medium text-emerald-200 hover:bg-emerald-500/10"
        >
          {l.label}
        </Link>
      ))}
    </div>
  );
}

function ReactionBar({
  reactions,
  onToggle,
}: {
  reactions?: { emoji: string; count: number; mine: boolean }[];
  onToggle: (emoji: string) => void;
}) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {(reactions ?? []).map((r) => (
        <button
          key={r.emoji}
          type="button"
          onClick={() => onToggle(r.emoji)}
          className={`rounded-full px-1.5 py-0.5 text-[11px] transition ${
            r.mine ? 'bg-emerald-500/25 text-emerald-100' : 'bg-black/25 text-zinc-300 hover:bg-white/10'
          }`}
        >
          {r.emoji} {r.count}
        </button>
      ))}
      <div className="group relative">
        <button
          type="button"
          className="rounded-full px-1.5 py-0.5 text-[11px] text-zinc-500 hover:bg-white/10 hover:text-zinc-300"
          title="Add reaction"
        >
          +
        </button>
        <div className="absolute bottom-full left-0 z-20 mb-1 hidden gap-0.5 rounded-lg border border-white/10 bg-[#1f2c34] p-1 shadow-lg group-hover:flex group-focus-within:flex">
          {REACTIONS.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => onToggle(e)}
              className="rounded px-1.5 py-0.5 text-sm hover:bg-white/10"
            >
              {e}
            </button>
          ))}
        </div>
      </div>
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
  const [dmPeer, setDmPeer] = useState<ConversationPeer | null>(null);
  const [groupMessages, setGroupMessages] = useState<WallMessage[]>([]);
  const [aggregated, setAggregated] = useState<WallMessage[]>([]);
  const [membership, setMembership] = useState<WallMembership | null>(null);
  const [draft, setDraft] = useState('');
  const [replyTo, setReplyTo] = useState<ReplyTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingThread, setLoadingThread] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsJoin, setNeedsJoin] = useState(false);
  const [joining, setJoining] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [threadSearch, setThreadSearch] = useState('');
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchHit, setSearchHit] = useState<{
    userId: string;
    label: string;
    platformHandle: string | null;
    isAdmin?: boolean;
    isVerifiedFounder?: boolean;
  } | null>(null);
  const [mobileShowThread, setMobileShowThread] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const deepLinkApplied = useRef(false);
  const activeRef = useRef(active);
  activeRef.current = active;

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
    async (otherUserId: string, soft = false) => {
      if (!token) return;
      if (!soft) setLoadingThread(true);
      try {
        const data = await fetchMessageConversation(otherUserId, token);
        setDmMessages(data.messages);
        setDmPeer(data.peer);
        setError(null);
        if (!soft) {
          await loadLists();
          dispatchInboxRefresh();
        }
      } catch (e) {
        if (!soft) {
          setError(e instanceof Error ? e.message : 'Could not load conversation');
          setDmMessages([]);
        }
      } finally {
        if (!soft) setLoadingThread(false);
      }
    },
    [token, loadLists],
  );

  const loadGroup = useCallback(
    async (slug: string, soft = false) => {
      if (!token) return;
      if (!soft) setLoadingThread(true);
      try {
        const [data, mem] = await Promise.all([
          fetchProjectWall(slug, token),
          fetchWallMembership(slug, token).catch(() => null),
        ]);
        setGroupMessages(data);
        setMembership(mem);
        setNeedsJoin(Boolean(mem && !mem.joined));
        setError(null);
        void markWallRead(slug, token).catch(() => {});
        setWallUnread((prev) => prev.filter((u) => u.slug !== slug));
      } catch (e) {
        if (!soft) {
          setError(e instanceof Error ? e.message : 'Could not load group');
          setGroupMessages([]);
        }
      } finally {
        if (!soft) setLoadingThread(false);
      }
    },
    [token],
  );

  const loadAllGroups = useCallback(
    async (soft = false) => {
      if (!token) return;
      if (!soft) setLoadingThread(true);
      try {
        const data = await fetchAggregatedWall(token, 100);
        setAggregated(data);
        setError(null);
      } catch (e) {
        if (!soft) {
          setError(e instanceof Error ? e.message : 'Could not load feed');
          setAggregated([]);
        }
      } finally {
        if (!soft) setLoadingThread(false);
      }
    },
    [token],
  );

  const refreshActive = useCallback(
    (soft = true) => {
      const cur = activeRef.current;
      if (!cur) return;
      if (cur.kind === 'dm') void loadDm(cur.userId, soft);
      else if (cur.kind === 'group') void loadGroup(cur.slug, soft);
      else if (cur.kind === 'all') void loadAllGroups(soft);
    },
    [loadDm, loadGroup, loadAllGroups],
  );

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
        projectId: g?.project.id,
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
      setReplyTo(null);
      setNeedsJoin(false);
      setMembership(null);
      setDmPeer(null);
      setThreadSearch('');
      return;
    }
    setDraft('');
    setReplyTo(null);
    setNeedsJoin(false);
    setThreadSearch('');
    if (active.kind === 'dm') void loadDm(active.userId);
    else if (active.kind === 'group') void loadGroup(active.slug);
    else if (active.kind === 'all') void loadAllGroups();
  }, [active, loadDm, loadGroup, loadAllGroups]);

  // Presence heartbeat + adaptive polling fallback
  useEffect(() => {
    if (!token) return;
    void chatPresenceHeartbeat(token).catch(() => {});
    const beat = setInterval(() => {
      void chatPresenceHeartbeat(token).catch(() => {});
    }, 60_000);
    return () => clearInterval(beat);
  }, [token]);

  useEffect(() => {
    if (!token || !active) return;
    const tick = () => {
      if (document.visibilityState === 'hidden') return;
      refreshActive(true);
      void loadLists();
    };
    const id = setInterval(tick, 3_000);
    return () => clearInterval(id);
  }, [token, active, refreshActive, loadLists]);

  // SSE live hints
  useEffect(() => {
    if (!token || typeof EventSource === 'undefined') return;
    let es: EventSource | null = null;
    try {
      es = new EventSource(chatEventsStreamUrl(token));
      es.addEventListener('chat', (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as {
            type?: string;
            otherUserId?: string;
            slug?: string;
          };
          if (data.type === 'ping') return;
          const cur = activeRef.current;
          if (data.type === 'dm' && cur?.kind === 'dm' && data.otherUserId === cur.userId) {
            refreshActive(true);
          } else if (data.type === 'wall' && cur?.kind === 'group' && data.slug === cur.slug) {
            refreshActive(true);
          } else if (data.type === 'wall' && cur?.kind === 'all') {
            refreshActive(true);
          } else if (data.type === 'prefs') {
            void loadLists();
          } else {
            void loadLists();
          }
        } catch {
          /* ignore */
        }
      });
    } catch {
      /* polling remains */
    }
    return () => es?.close();
  }, [token, refreshActive, loadLists]);

  useEffect(() => {
    if (scrollRef.current && !threadSearch.trim()) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [dmMessages.length, groupMessages.length, aggregated.length, active, threadSearch]);

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
      if (!showArchived && t.archived) return false;
      if (showArchived && !t.archived) return false;
      if (unreadOnly && t.unreadCount <= 0) return false;
      if (!q) return true;
      return (
        t.otherUserLabel.toLowerCase().includes(q) || t.lastBody.toLowerCase().includes(q)
      );
    });
  }, [threads, searchQuery, unreadOnly, showArchived]);

  const filteredGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return groups.filter((g) => {
      if (!showArchived && g.archived) return false;
      if (showArchived && !g.archived) return false;
      const unread = wallUnread.find((u) => u.slug === g.project.slug)?.unreadCount ?? g.unreadCount ?? 0;
      if (unreadOnly && unread <= 0) return false;
      if (!q) return true;
      return (
        g.project.name.toLowerCase().includes(q) ||
        g.project.ticker.toLowerCase().includes(q) ||
        (g.project.founder?.name ?? '').toLowerCase().includes(q)
      );
    });
  }, [groups, searchQuery, unreadOnly, wallUnread, showArchived]);

  const visibleDm = useMemo(() => {
    const q = threadSearch.trim().toLowerCase();
    if (!q) return dmMessages;
    return dmMessages.filter(
      (m) => m.body.toLowerCase().includes(q) || m.fromLabel.toLowerCase().includes(q),
    );
  }, [dmMessages, threadSearch]);

  const visibleGroup = useMemo(() => {
    const q = threadSearch.trim().toLowerCase();
    if (!q) return groupMessages;
    return groupMessages.filter((m) => {
      const author = m.author.name ?? m.author.platformHandle ?? '';
      return m.body.toLowerCase().includes(q) || author.toLowerCase().includes(q);
    });
  }, [groupMessages, threadSearch]);

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
        isAdmin: res.isAdmin,
        isVerifiedFounder: res.isVerifiedFounder,
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
        await sendPlatformMessage(active.userId, body, token, undefined, replyTo?.id);
        setDraft('');
        setReplyTo(null);
        await loadDm(active.userId);
      } else if (active.kind === 'group') {
        try {
          const msg = await postProjectWallMessage(active.slug, body, token, replyTo?.id);
          setGroupMessages((prev) => [...prev, msg]);
          setDraft('');
          setReplyTo(null);
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
        const msg = await postProjectWallMessage(active.slug, draft.trim(), token, replyTo?.id);
        setGroupMessages((prev) => [...prev, msg]);
        setDraft('');
        setReplyTo(null);
      } else {
        await loadGroup(active.slug);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to join');
    } finally {
      setJoining(false);
    }
  }

  async function togglePref(
    scope: 'dm' | 'wall',
    targetId: string,
    patch: { pinned?: boolean; muted?: boolean; archived?: boolean },
  ) {
    if (!token) return;
    try {
      await setChatThreadPref(token, { scope, targetId, ...patch });
      await loadLists();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update chat');
    }
  }

  async function handleMarkAllRead() {
    if (!token) return;
    try {
      await Promise.all([markAllMessagesRead(token), markAllWallsRead(token)]);
      await loadLists();
      dispatchInboxRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not mark read');
    }
  }

  const wallUnreadFor = (slug: string) =>
    wallUnread.find((u) => u.slug === slug)?.unreadCount ??
    groups.find((g) => g.project.slug === slug)?.unreadCount ??
    0;

  const headerTitle = useMemo(() => {
    if (!active) return null;
    if (active.kind === 'dm') return active.label;
    if (active.kind === 'group') return active.name;
    return 'All groups';
  }, [active]);

  const headerSub = useMemo(() => {
    if (!active) return null;
    if (active.kind === 'dm') {
      if (dmPeer?.online) return 'Online';
      if (dmPeer?.lastSeenAt) return `Last seen ${formatRelativeTime(dmPeer.lastSeenAt)}`;
      return 'Direct message';
    }
    if (active.kind === 'group') {
      const mode = membership?.postingMode === 'ANNOUNCEMENTS' ? ' · Announcements' : '';
      const slow =
        membership?.slowModeSeconds && membership.slowModeSeconds > 0
          ? ` · Slow ${membership.slowModeSeconds}s`
          : '';
      return `${active.ticker ? `#${active.ticker}` : 'Project wall'}${mode}${slow}`;
    }
    return 'Unified feed across your project groups';
  }, [active, dmPeer, membership]);

  const activeDmPref = active?.kind === 'dm' ? threads.find((t) => t.otherUserId === active.userId) : null;
  const activeGroupPref =
    active?.kind === 'group' ? groups.find((g) => g.project.slug === active.slug) : null;

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

  const canCompose =
    active &&
    active.kind !== 'all' &&
    !needsJoin &&
    (active.kind === 'dm' || membership?.canPost !== false);

  return (
    <div className="flex h-[100dvh] flex-col bg-[#0b141a] text-zinc-100">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/5 bg-[#111b21] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <SiteBrand className="text-sm" />
          <span className="hidden h-4 w-px bg-zinc-700 sm:block" />
          <h1 className="truncate text-sm font-semibold text-white">Chat</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleMarkAllRead()}
            className="hidden rounded-lg px-2.5 py-1.5 text-xs text-zinc-400 transition hover:bg-white/5 hover:text-white sm:inline"
          >
            Mark all read
          </button>
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
                  onClick={() => setShowArchived((v) => !v)}
                  className={`rounded-lg px-2 py-1 text-[11px] font-semibold transition ${
                    showArchived
                      ? 'bg-zinc-600/40 text-zinc-200'
                      : 'text-zinc-500 hover:bg-white/5 hover:text-zinc-300'
                  }`}
                  title="Show archived"
                >
                  <Archive className="h-3.5 w-3.5" />
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
                <div className="flex items-center gap-1.5">
                  <p className="truncate text-sm font-semibold text-white">{searchHit.label}</p>
                  <BadgeRow isAdmin={searchHit.isAdmin} isVerifiedFounder={searchHit.isVerifiedFounder} />
                </div>
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
              {threads.some((t) => t.unreadCount > 0 && !t.archived) && (
                <span className="rounded-full bg-emerald-500 px-1.5 text-[9px] text-black">
                  {threads.reduce((n, t) => n + (t.archived ? 0 : t.unreadCount), 0)}
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
                  <p className="text-sm text-zinc-400">
                    {showArchived ? 'No archived chats.' : 'No direct messages yet.'}
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
                      <Avatar label={t.otherUserLabel} tone="cyan" online={t.online} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex min-w-0 items-center gap-1">
                            {t.pinned ? <Pin className="h-3 w-3 shrink-0 text-emerald-400" /> : null}
                            {t.muted ? <BellOff className="h-3 w-3 shrink-0 text-zinc-500" /> : null}
                            <p
                              className={`truncate text-sm ${
                                t.unreadCount > 0 ? 'font-semibold text-white' : 'font-medium text-zinc-200'
                              }`}
                            >
                              {t.otherUserLabel}
                            </p>
                            <BadgeRow isAdmin={t.isAdmin} isVerifiedFounder={t.isVerifiedFounder} />
                          </div>
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
                {!showArchived && (
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
                )}
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
                    const verified = g.project.founder?.presenceLevel
                      ? g.project.founder.presenceLevel !== 'UNVERIFIED'
                      : false;
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
                            projectId: g.project.id,
                          })
                        }
                        className={`mb-0.5 flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition ${
                          selected ? 'bg-[#2a3942]' : 'hover:bg-white/[0.04]'
                        }`}
                      >
                        <Avatar label={g.project.ticker} logoUrl={g.project.logoUrl} tone="emerald" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex min-w-0 items-center gap-1">
                              {g.pinned ? <Pin className="h-3 w-3 shrink-0 text-emerald-400" /> : null}
                              <p
                                className={`truncate text-sm ${
                                  unread > 0 ? 'font-semibold text-white' : 'font-medium text-zinc-200'
                                }`}
                              >
                                {g.project.name}
                              </p>
                              <BadgeRow isVerifiedFounder={verified} />
                            </div>
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
              <div className="flex shrink-0 flex-col gap-2 border-b border-white/5 bg-[#202c33]/90 px-3 py-2.5 backdrop-blur">
                <div className="flex items-center gap-3">
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
                    online={active.kind === 'dm' ? Boolean(dmPeer?.online) : false}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="truncate text-sm font-semibold text-white">{headerTitle}</p>
                      {active.kind === 'dm' && (
                        <BadgeRow
                          isAdmin={dmPeer?.isAdmin ?? activeDmPref?.isAdmin}
                          isVerifiedFounder={
                            dmPeer?.isVerifiedFounder ?? activeDmPref?.isVerifiedFounder
                          }
                        />
                      )}
                    </div>
                    <p className="truncate text-[11px] text-zinc-400">{headerSub}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {active.kind === 'dm' && (
                      <>
                        <button
                          type="button"
                          title={activeDmPref?.pinned ? 'Unpin' : 'Pin (free, max 20)'}
                          onClick={() =>
                            void togglePref('dm', active.userId, {
                              pinned: !activeDmPref?.pinned,
                            })
                          }
                          className={`rounded-lg p-1.5 ${activeDmPref?.pinned ? 'text-emerald-300' : 'text-zinc-400 hover:text-white'}`}
                        >
                          <Pin className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          title={activeDmPref?.muted ? 'Unmute' : 'Mute'}
                          onClick={() =>
                            void togglePref('dm', active.userId, { muted: !activeDmPref?.muted })
                          }
                          className={`rounded-lg p-1.5 ${activeDmPref?.muted ? 'text-amber-300' : 'text-zinc-400 hover:text-white'}`}
                        >
                          <BellOff className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          title={activeDmPref?.archived ? 'Unarchive' : 'Archive'}
                          onClick={() =>
                            void togglePref('dm', active.userId, {
                              archived: !activeDmPref?.archived,
                            })
                          }
                          className="rounded-lg p-1.5 text-zinc-400 hover:text-white"
                        >
                          <Archive className="h-4 w-4" />
                        </button>
                      </>
                    )}
                    {active.kind === 'group' && activeGroupPref && (
                      <>
                        <button
                          type="button"
                          title={activeGroupPref.pinned ? 'Unpin' : 'Pin'}
                          onClick={() =>
                            void togglePref('wall', activeGroupPref.project.id, {
                              pinned: !activeGroupPref.pinned,
                            })
                          }
                          className={`rounded-lg p-1.5 ${activeGroupPref.pinned ? 'text-emerald-300' : 'text-zinc-400 hover:text-white'}`}
                        >
                          <Pin className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          title={activeGroupPref.muted ? 'Unmute' : 'Mute'}
                          onClick={() =>
                            void togglePref('wall', activeGroupPref.project.id, {
                              muted: !activeGroupPref.muted,
                            })
                          }
                          className={`rounded-lg p-1.5 ${activeGroupPref.muted ? 'text-amber-300' : 'text-zinc-400 hover:text-white'}`}
                        >
                          <BellOff className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          title={activeGroupPref.archived ? 'Unarchive' : 'Archive'}
                          onClick={() =>
                            void togglePref('wall', activeGroupPref.project.id, {
                              archived: !activeGroupPref.archived,
                            })
                          }
                          className="rounded-lg p-1.5 text-zinc-400 hover:text-white"
                        >
                          <Archive className="h-4 w-4" />
                        </button>
                      </>
                    )}
                    {active.kind === 'group' && (
                      <Link
                        href={`/project/${active.slug}`}
                        className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-emerald-300 hover:bg-white/5"
                      >
                        Project
                      </Link>
                    )}
                  </div>
                </div>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
                  <input
                    value={threadSearch}
                    onChange={(e) => setThreadSearch(e.target.value)}
                    placeholder="Search in this chat…"
                    className="w-full rounded-lg border border-transparent bg-[#111b21] py-1.5 pl-8 pr-3 text-xs text-white placeholder:text-zinc-500 focus:border-emerald-500/30 focus:outline-none"
                  />
                </div>
                {active.kind === 'group' && membership?.isFounder && (
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="text-zinc-500">Wall mode:</span>
                    <button
                      type="button"
                      onClick={() =>
                        token &&
                        void updateWallSettings(
                          active.slug,
                          { postingMode: 'OPEN' },
                          token,
                        ).then(() => loadGroup(active.slug))
                      }
                      className={`rounded px-2 py-0.5 ${
                        membership.postingMode !== 'ANNOUNCEMENTS'
                          ? 'bg-emerald-600/30 text-emerald-200'
                          : 'bg-white/5 text-zinc-400'
                      }`}
                    >
                      Open discussion
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        token &&
                        void updateWallSettings(
                          active.slug,
                          { postingMode: 'ANNOUNCEMENTS' },
                          token,
                        ).then(() => loadGroup(active.slug))
                      }
                      className={`rounded px-2 py-0.5 ${
                        membership.postingMode === 'ANNOUNCEMENTS'
                          ? 'bg-amber-600/30 text-amber-200'
                          : 'bg-white/5 text-zinc-400'
                      }`}
                    >
                      Announcements only
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        token &&
                        void updateWallSettings(
                          active.slug,
                          {
                            slowModeSeconds:
                              (membership.slowModeSeconds ?? 0) > 0 ? 0 : 60,
                          },
                          token,
                        ).then(() => loadGroup(active.slug))
                      }
                      className="rounded bg-white/5 px-2 py-0.5 text-zinc-400 hover:text-white"
                    >
                      {(membership.slowModeSeconds ?? 0) > 0 ? 'Disable slow mode' : 'Slow mode 60s'}
                    </button>
                  </div>
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
                  visibleDm.map((m, i) => {
                    const prev = visibleDm[i - 1];
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
                              <div className="mb-0.5 flex flex-wrap items-center gap-1 text-[10px]">
                                <span className="font-semibold text-cyan-300/90">{m.fromLabel}</span>
                                <BadgeRow isAdmin={m.isAdmin} isVerifiedFounder={m.isVerifiedFounder} />
                              </div>
                            )}
                            {m.replyTo && (
                              <div className="mb-1 rounded-lg border-l-2 border-emerald-400/50 bg-black/20 px-2 py-1 text-[11px] text-zinc-400">
                                <p className="font-semibold text-zinc-300">{m.replyTo.fromLabel}</p>
                                <p className="truncate">{m.replyTo.body}</p>
                              </div>
                            )}
                            <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                            <PlatformLinkPreviews text={m.body} />
                            <ReactionBar
                              reactions={m.reactions}
                              onToggle={(emoji) => {
                                if (!token) return;
                                void reactToDmMessage(m.id, emoji, token).then(() =>
                                  loadDm(active.userId, true),
                                );
                              }}
                            />
                            <div
                              className={`mt-1 flex items-center justify-end gap-2 text-[10px] ${
                                m.mine ? 'text-emerald-200/60' : 'text-zinc-500'
                              }`}
                            >
                              <button
                                type="button"
                                className="hover:text-white"
                                onClick={() =>
                                  setReplyTo({
                                    id: m.id,
                                    preview: m.body.slice(0, 80),
                                    authorLabel: m.fromLabel,
                                  })
                                }
                              >
                                <Reply className="h-3 w-3" />
                              </button>
                              <span>{fmtClock(m.createdAt)}</span>
                              {m.mine &&
                                (m.readAt ? (
                                  <CheckCheck className="h-3.5 w-3.5 text-sky-300" aria-label="Read" />
                                ) : (
                                  <Check className="h-3.5 w-3.5" aria-label="Sent" />
                                ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}

                {active.kind === 'group' &&
                  visibleGroup.map((m, i) => {
                    const mine = myId === m.authorId;
                    const prev = visibleGroup[i - 1];
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
                                <BadgeRow
                                  isAdmin={m.author.isAdmin}
                                  isVerifiedFounder={m.author.isVerifiedFounder}
                                />
                              </div>
                            )}
                            {m.replyTo && (
                              <div className="mb-1 rounded-lg border-l-2 border-emerald-400/50 bg-black/20 px-2 py-1 text-[11px] text-zinc-400">
                                <p className="font-semibold text-zinc-300">{m.replyTo.authorLabel}</p>
                                <p className="truncate">{m.replyTo.body}</p>
                              </div>
                            )}
                            <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                            <PlatformLinkPreviews text={m.body} />
                            {m.links && (
                              <div className="mt-1.5 flex flex-wrap gap-1">
                                <Link
                                  href={m.links.project}
                                  className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-black/40"
                                >
                                  Project
                                </Link>
                                <Link
                                  href={m.links.raiseRoom}
                                  className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-black/40"
                                >
                                  Raise room
                                </Link>
                                {m.links.founderSpotlight && (
                                  <Link
                                    href={m.links.founderSpotlight}
                                    className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-black/40"
                                  >
                                    Founder
                                  </Link>
                                )}
                              </div>
                            )}
                            <ReactionBar
                              reactions={m.reactions}
                              onToggle={(emoji) => {
                                if (!token) return;
                                void reactToWallMessage(m.id, emoji, token).then(() =>
                                  loadGroup(active.slug, true),
                                );
                              }}
                            />
                            <div
                              className={`mt-1 flex items-center justify-end gap-2 text-[10px] ${
                                mine ? 'text-emerald-200/60' : 'text-zinc-500'
                              }`}
                            >
                              <button
                                type="button"
                                className="hover:text-white"
                                onClick={() =>
                                  setReplyTo({
                                    id: m.id,
                                    preview: m.body.slice(0, 80),
                                    authorLabel: m.author.name ?? m.author.platformHandle ?? 'Anon',
                                  })
                                }
                              >
                                <Reply className="h-3 w-3" />
                              </button>
                              {!mine && (
                                <button
                                  type="button"
                                  className="hover:text-red-300"
                                  title="Report"
                                  onClick={() => {
                                    if (!token) return;
                                    const reason = window.prompt('Why are you reporting this message?');
                                    if (!reason) return;
                                    void reportWallMessage(m.id, reason, token).then(() =>
                                      loadGroup(active.slug, true),
                                    );
                                  }}
                                >
                                  Report
                                </button>
                              )}
                              <span>{fmtClock(m.createdAt)}</span>
                            </div>
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
                                <BadgeRow
                                  isAdmin={m.author.isAdmin}
                                  isVerifiedFounder={m.author.isVerifiedFounder}
                                />
                              </div>
                              <p className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</p>
                              {m.links && (
                                <div className="mt-1.5 flex flex-wrap gap-1">
                                  <Link
                                    href={m.links.project}
                                    className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300"
                                  >
                                    Project
                                  </Link>
                                  <Link
                                    href={m.links.raiseRoom}
                                    className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300"
                                  >
                                    Raise
                                  </Link>
                                  {m.links.founderSpotlight && (
                                    <Link
                                      href={m.links.founderSpotlight}
                                      className="rounded bg-black/25 px-1.5 py-0.5 text-[10px] text-emerald-300"
                                    >
                                      Founder
                                    </Link>
                                  )}
                                </div>
                              )}
                              <p className="mt-1 text-right text-[10px] text-zinc-500">
                                {fmtClock(m.createdAt)}
                              </p>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ))}

                {active.kind === 'dm' && !loadingThread && visibleDm.length === 0 && (
                  <div className="py-16 text-center text-sm text-zinc-500">
                    {threadSearch ? 'No matches in this chat.' : 'No messages yet — say hello.'}
                  </div>
                )}
                {active.kind === 'group' && !loadingThread && visibleGroup.length === 0 && (
                  <div className="py-16 text-center text-sm text-zinc-500">
                    {threadSearch ? 'No matches in this wall.' : 'No messages on this wall yet.'}
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
              ) : membership && membership.canPost === false && active.kind === 'group' ? (
                <div className="shrink-0 border-t border-white/5 bg-[#111b21] px-4 py-3 text-center text-[11px] text-zinc-400">
                  {membership.mutedUntil
                    ? `You are muted on this wall until ${new Date(membership.mutedUntil).toLocaleString()}.`
                    : membership.postingMode === 'ANNOUNCEMENTS'
                      ? 'Announcements mode — only the founder can post right now.'
                      : 'You cannot post on this wall right now.'}
                </div>
              ) : canCompose ? (
                <form
                  onSubmit={handleSend}
                  className="shrink-0 border-t border-white/5 bg-[#111b21] px-3 py-2.5 sm:px-4"
                >
                  {replyTo && (
                    <div className="mx-auto mb-2 flex max-w-3xl items-center gap-2 rounded-lg border border-emerald-500/20 bg-[#202c33] px-3 py-1.5 text-[11px]">
                      <Reply className="h-3.5 w-3.5 text-emerald-400" />
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-emerald-300">Replying to {replyTo.authorLabel}</p>
                        <p className="truncate text-zinc-400">{replyTo.preview}</p>
                      </div>
                      <button type="button" onClick={() => setReplyTo(null)} className="text-zinc-500 hover:text-white">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
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
                      maxLength={active.kind === 'dm' ? 4000 : 4000}
                      placeholder={
                        active.kind === 'dm'
                          ? `Message ${active.label}…`
                          : active.kind === 'group'
                            ? `Message ${active.name}… (@handle to mention)`
                            : 'Message…'
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
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

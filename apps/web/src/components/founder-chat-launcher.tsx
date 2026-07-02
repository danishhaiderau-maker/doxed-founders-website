'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Send, X, Loader2, MessageSquare, Hash, ShieldCheck, ChevronDown } from 'lucide-react';
import {
  fetchAggregatedWall,
  fetchMyWallGroups,
  fetchMyWallUnread,
  fetchProjectWall,
  joinProjectWall,
  markWallRead,
  postProjectWallMessage,
  type WallGroupEntry,
  type WallMessage,
  type WallUnreadEntry,
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

function GroupRow({
  entry,
  active,
  unread,
  onClick,
}: {
  entry: WallGroupEntry;
  active: boolean;
  unread: number;
  onClick: () => void;
}) {
  const last = entry.lastMessage;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition ${
        active ? 'border-violet-500/50 bg-violet-500/10' : unread > 0 ? 'border-emerald-500/20 bg-emerald-500/[0.04]' : 'border-transparent hover:bg-zinc-900/70'
      }`}
    >
      <div className={`relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-zinc-800 text-xs font-bold text-violet-300`}>
        {entry.project.logoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={entry.project.logoUrl} alt="" className="h-9 w-9 rounded-lg object-cover" />
        ) : (
          entry.project.ticker.slice(0, 2)
        )}
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full border border-[#0B0B0B] bg-red-500 px-1 text-[9px] font-bold leading-none text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className={`truncate text-sm ${unread > 0 ? 'font-semibold text-white' : 'font-medium text-white'}`}>{entry.project.name}</p>
          <span className="shrink-0 text-[10px] text-zinc-600">
            {last ? fmtTime(last.createdAt) : ''}
          </span>
        </div>
        <p className={`truncate text-[11px] ${unread > 0 ? 'text-zinc-300' : 'text-zinc-500'}`}>
          {last ? last.body : `${entry.project.ticker} · join the conversation`}
        </p>
      </div>
    </button>
  );
}

function AggregatedBubble({ m, mine }: { m: WallMessage; mine: boolean }) {
  return (
    <div className={`flex items-start gap-2.5 ${mine ? 'flex-row-reverse' : ''}`}>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-[10px] font-bold text-zinc-300">
        {m.projectLogoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={m.projectLogoUrl} alt="" className="h-8 w-8 rounded-full object-cover" />
        ) : (
          m.projectTicker.slice(0, 2)
        )}
      </div>
      <div className={`max-w-[80%] ${mine ? 'items-end text-right' : 'items-start'} flex flex-col`}>
        <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          <Link
            href={`/project/${m.projectSlug}`}
            className="font-medium text-violet-300 hover:underline"
          >
            {m.projectName}
          </Link>
          <span className="text-zinc-600">·</span>
          <span className="font-medium text-zinc-400">
            {m.author.name ?? m.author.platformHandle ?? 'Anon'}
          </span>
          {m.author.isVerifiedFounder && (
            <span className="inline-flex items-center gap-0.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1 py-px text-[9px] font-semibold text-emerald-300">
              <ShieldCheck className="h-2.5 w-2.5" /> FOUNDER
            </span>
          )}
          <span>{fmtTime(m.createdAt)}</span>
        </div>
        <div
          className={`mt-1 rounded-2xl border px-3 py-2 text-sm leading-relaxed ${
            mine
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-50'
              : 'border-zinc-700/70 bg-zinc-900/70 text-zinc-100'
          }`}
        >
          <p className="whitespace-pre-wrap break-words">{m.body}</p>
        </div>
      </div>
    </div>
  );
}

export function FounderChatLauncher() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [open, setOpen] = useState(false);
  const [groups, setGroups] = useState<WallGroupEntry[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [activeSlug, setActiveSlug] = useState<string | null>(null); // null = aggregated "All"
  const [aggregated, setAggregated] = useState<WallMessage[]>([]);
  const [roomMessages, setRoomMessages] = useState<WallMessage[]>([]);
  const [roomLoading, setRoomLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [composer, setComposer] = useState('');
  const [posting, setPosting] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [needsJoin, setNeedsJoin] = useState(false);
  const [joining, setJoining] = useState(false);
  const [unread, setUnread] = useState<WallUnreadEntry[]>([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [jumpOpen, setJumpOpen] = useState(false);
  const jumpRef = useRef<HTMLDivElement>(null);

  const loadGroups = useCallback(async () => {
    if (!token) return;
    setGroupsLoading(true);
    try {
      const data = await fetchMyWallGroups(token);
      setGroups(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load groups');
    } finally {
      setGroupsLoading(false);
    }
  }, [token]);

  const loadAggregated = useCallback(async () => {
    if (!token) return;
    try {
      const data = await fetchAggregatedWall(token, 80);
      setAggregated(data);
    } catch {
      setAggregated([]);
    }
  }, [token]);

  const loadUnread = useCallback(async () => {
    if (!token) return;
    try {
      const data = await fetchMyWallUnread(token);
      setUnread(data.projects);
      setUnreadTotal(data.total);
    } catch {
      /* non-fatal */
    }
  }, [token]);

  const loadRoom = useCallback(async (slug: string) => {
    setRoomLoading(true);
    try {
      const data = await fetchProjectWall(slug, token);
      setRoomMessages(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load wall');
      setRoomMessages([]);
    } finally {
      setRoomLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (open && token) {
      void loadGroups();
      void loadAggregated();
      void loadUnread();
    }
  }, [open, token, loadGroups, loadAggregated, loadUnread]);

  useEffect(() => {
    if (open && activeSlug) void loadRoom(activeSlug);
    if (open && !activeSlug) setRoomMessages([]);
  }, [open, activeSlug, loadRoom]);

  // Poll the active view while drawer is open.
  useEffect(() => {
    if (!open || !token) return;
    const id = setInterval(() => {
      if (activeSlug) void loadRoom(activeSlug);
      else void loadAggregated();
      void loadUnread();
    }, 9000);
    return () => clearInterval(id);
  }, [open, token, activeSlug, loadRoom, loadAggregated, loadUnread]);

  // Mark a project read when it becomes the active room (clears its unread badge).
  useEffect(() => {
    if (!token || !activeSlug) return;
    setUnread((prev) => prev.filter((u) => u.slug !== activeSlug));
    setUnreadTotal((prev) => Math.max(0, prev - (unread.find((u) => u.slug === activeSlug)?.unreadCount ?? 0)));
    void markWallRead(activeSlug, token).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSlug, token]);

  // Close the quick-jump dropdown on outside click.
  useEffect(() => {
    if (!jumpOpen) return;
    const handler = (e: MouseEvent) => {
      if (jumpRef.current && !jumpRef.current.contains(e.target as Node)) setJumpOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [jumpOpen]);

  const unreadFor = (slug: string) => unread.find((u) => u.slug === slug)?.unreadCount ?? 0;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [activeSlug, roomMessages.length, aggregated.length]);

  // Lock body scroll while drawer open.
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  // Reset composer state when switching rooms.
  useEffect(() => {
    setComposer('');
    setPostError(null);
    setNeedsJoin(false);
  }, [activeSlug]);

  const submitPost = useCallback(async () => {
    if (!token || !activeSlug) return;
    const body = composer.trim();
    if (!body) return;
    setPosting(true);
    setPostError(null);
    try {
      const msg = await postProjectWallMessage(activeSlug, body, token);
      setRoomMessages((prev) => [...prev, msg]);
      setAggregated((prev) => [msg, ...prev].slice(0, 120));
      setComposer('');
      setNeedsJoin(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to post';
      if (/join|follower|forbidden|403/i.test(message)) {
        setNeedsJoin(true);
      }
      setPostError(message);
    } finally {
      setPosting(false);
    }
  }, [token, activeSlug, composer]);

  const joinAndRetry = useCallback(async () => {
    if (!token || !activeSlug) return;
    setJoining(true);
    setPostError(null);
    try {
      await joinProjectWall(activeSlug, token);
      setNeedsJoin(false);
      // Refresh groups so the joined project shows in the rail, then retry the post.
      void loadGroups();
      await submitPost();
    } catch (err) {
      setPostError(err instanceof Error ? err.message : 'Failed to join project');
    } finally {
      setJoining(false);
    }
  }, [token, activeSlug, loadGroups, submitPost]);

  const myId = session?.user?.id;
  const activeGroup = useMemo(() => groups.find((g) => g.project.slug === activeSlug) ?? null, [groups, activeSlug]);

  if (!token) {
    return (
      <Link
        href="/login?callbackUrl=/founder-chat"
        className="rounded-lg px-2.5 py-1.5 text-zinc-300 transition hover:bg-zinc-800 hover:text-white"
        title="Sign in for Founder Chat"
        aria-label="Founder Chat — sign in"
      >
        <Send className="h-4 w-4" />
      </Link>
    );
  }

  return (
    <>
      <div ref={jumpRef} className="relative inline-flex items-center">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="relative inline-flex items-center gap-1.5 rounded-l-lg px-2.5 py-1.5 text-zinc-200 transition hover:bg-violet-500/10 hover:text-white"
          title="Founder Chat — your project groups"
          aria-label="Founder Chat"
        >
          <Send className="h-4 w-4 text-violet-300" />
          <span className="hidden text-xs font-semibold lg:inline">Chat</span>
          {unreadTotal > 0 && (
            <span className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full border border-red-400/40 bg-red-500 px-1 text-[9px] font-bold leading-none text-white">
              {unreadTotal > 9 ? '9+' : unreadTotal}
            </span>
          )}
          {groups.length > 0 && unreadTotal === 0 && (
            <span className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full border border-violet-300/40 bg-violet-600 px-1 text-[9px] font-bold leading-none text-white">
              {groups.length > 9 ? '9+' : groups.length}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            setJumpOpen((v) => !v);
            if (!groups.length && token) void loadGroups();
          }}
          className="inline-flex items-center rounded-r-lg border-l border-zinc-700/60 px-1 py-1.5 text-zinc-300 transition hover:bg-violet-500/10 hover:text-white"
          title="Quick jump to a project wall"
          aria-label="Quick jump to project wall"
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>

        {jumpOpen && (
          <div className="absolute right-0 top-full z-[80] mt-1 w-64 overflow-hidden rounded-xl border border-zinc-700 bg-[#0B0B0B] shadow-2xl">
            <div className="border-b border-zinc-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
              Jump to project wall
            </div>
            <div className="max-h-72 overflow-y-auto py-1">
              {groups.length === 0 && (
                <p className="px-3 py-3 text-xs text-zinc-600">
                  No groups yet.{' '}
                  <Link href="/projects" className="text-violet-400 hover:underline" onClick={() => setJumpOpen(false)}>
                    Browse projects
                  </Link>
                </p>
              )}
              {groups.map((g) => {
                const u = unreadFor(g.project.slug);
                return (
                  <Link
                    key={g.project.id}
                    href={`/project/${g.project.slug}`}
                    onClick={() => setJumpOpen(false)}
                    className="flex items-center gap-2 px-3 py-2 text-left transition hover:bg-zinc-900"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-zinc-800 text-[10px] font-bold text-violet-300">
                      {g.project.logoUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={g.project.logoUrl} alt="" className="h-7 w-7 rounded-md object-cover" />
                      ) : (
                        g.project.ticker.slice(0, 2)
                      )}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-zinc-200">{g.project.name}</span>
                    {u > 0 && (
                      <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold leading-none text-white">
                        {u > 9 ? '9+' : u}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {open && (
        <div className="fixed inset-0 z-[75] flex">
          <button
            type="button"
            aria-label="Close Founder Chat"
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />
          <aside className="relative ml-auto flex h-full w-full max-w-3xl flex-col border-l border-violet-500/30 bg-[#0B0B0B] shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div className="flex items-center gap-2">
                <Send className="h-4 w-4 text-violet-400" />
                <h2 className="text-sm font-bold text-white">Founder Chat</h2>
                <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-px text-[10px] font-bold text-emerald-300">
                  {groups.length} group{groups.length === 1 ? '' : 's'}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex min-h-0 flex-1">
              {/* Group rail */}
              <div className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950/40">
                <button
                  type="button"
                  onClick={() => setActiveSlug(null)}
                  className={`flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition ${
                    activeSlug === null ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-transparent hover:bg-zinc-900/70'
                  }`}
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-300">
                    <Hash className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-white">All</p>
                    <p className="truncate text-[11px] text-zinc-500">Unified wall</p>
                  </div>
                </button>
                <div className="flex-1 overflow-y-auto px-1.5 py-1.5">
                  {groupsLoading && groups.length === 0 && (
                    <div className="flex items-center justify-center gap-2 py-6 text-xs text-zinc-600">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
                    </div>
                  )}
                  {!groupsLoading && groups.length === 0 && (
                    <p className="px-3 py-6 text-center text-xs text-zinc-600">
                      You haven&apos;t joined any project groups yet.{' '}
                      <Link href="/projects" className="text-violet-400 hover:underline" onClick={() => setOpen(false)}>
                        Browse projects
                      </Link>
                    </p>
                  )}
                  {groups.map((g) => (
                    <GroupRow
                      key={g.project.id}
                      entry={g}
                      active={activeSlug === g.project.slug}
                      unread={unreadFor(g.project.slug)}
                      onClick={() => setActiveSlug(g.project.slug)}
                    />
                  ))}
                </div>
              </div>

              {/* Active wall */}
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    {activeGroup ? (
                      <>
                        <span className="truncate text-sm font-semibold text-white">{activeGroup.project.name}</span>
                        <span className="text-xs text-zinc-500">{activeGroup.project.ticker}</span>
                        {activeGroup.project.founder?.presenceLevel &&
                          activeGroup.project.founder.presenceLevel !== 'UNVERIFIED' && (
                            <span className="inline-flex items-center gap-0.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1 py-px text-[9px] font-semibold text-emerald-300">
                              <ShieldCheck className="h-2.5 w-2.5" /> VERIFIED
                            </span>
                          )}
                      </>
                    ) : (
                      <span className="text-sm font-semibold text-white">All groups</span>
                    )}
                  </div>
                  {activeGroup && (
                    <Link
                      href={`/project/${activeGroup.project.slug}`}
                      onClick={() => setOpen(false)}
                      className="shrink-0 text-[11px] font-medium text-violet-400 hover:underline"
                    >
                      Open project →
                    </Link>
                  )}
                </div>

                <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
                  {error && (
                    <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-xs text-red-300">{error}</p>
                  )}
                  {activeSlug === null ? (
                    aggregated.length === 0 && !error ? (
                      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                        <MessageSquare className="h-8 w-8 text-zinc-700" />
                        <p className="text-sm text-zinc-500">No messages across your groups yet.</p>
                        <p className="text-xs text-zinc-600">Join a project and start the conversation.</p>
                      </div>
                    ) : (
                      aggregated.map((m) => <AggregatedBubble key={m.id} m={m} mine={myId === m.authorId} />)
                    )
                  ) : roomLoading && roomMessages.length === 0 ? (
                    <div className="flex items-center justify-center gap-2 py-16 text-xs text-zinc-600">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading wall…
                    </div>
                  ) : roomMessages.length === 0 && !error ? (
                    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                      <MessageSquare className="h-8 w-8 text-zinc-700" />
                      <p className="text-sm text-zinc-500">No messages yet on this wall.</p>
                    </div>
                  ) : (
                    roomMessages.map((m) => <AggregatedBubble key={m.id} m={m} mine={myId === m.authorId} />)
                  )}
                </div>

                {activeSlug && (
                  <div className="border-t border-zinc-800 px-3 py-2.5">
                    {needsJoin ? (
                      <div className="flex flex-col items-center gap-2 rounded-lg border border-violet-500/30 bg-violet-500/5 px-3 py-2.5 text-center">
                        <p className="text-xs text-zinc-300">
                          You need to join this project to post on its wall.
                        </p>
                        <button
                          type="button"
                          onClick={joinAndRetry}
                          disabled={joining}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-violet-500/50 bg-violet-600/20 px-3 py-1.5 text-xs font-semibold text-violet-200 transition hover:bg-violet-600/30 disabled:opacity-50"
                        >
                          {joining ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                          Join &amp; post
                        </button>
                      </div>
                    ) : (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void submitPost();
                        }}
                        className="flex items-center gap-2"
                      >
                        <input
                          type="text"
                          value={composer}
                          onChange={(e) => setComposer(e.target.value)}
                          maxLength={500}
                          placeholder={`Message ${activeGroup?.project.name ?? 'group'}…`}
                          className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-900/80 px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:border-violet-500/60 focus:outline-none"
                        />
                        <button
                          type="submit"
                          disabled={posting || !composer.trim()}
                          className="inline-flex shrink-0 items-center justify-center rounded-lg border border-violet-500/50 bg-violet-600/20 p-2 text-violet-200 transition hover:bg-violet-600/30 disabled:cursor-not-allowed disabled:opacity-40"
                          aria-label="Send message"
                        >
                          {posting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        </button>
                      </form>
                    )}
                    {postError && !needsJoin && (
                      <p className="mt-1.5 text-[11px] text-red-400">{postError}</p>
                    )}
                  </div>
                )}
                {activeSlug === null && aggregated.length > 0 && (
                  <div className="border-t border-zinc-800 px-3 py-2.5 text-center text-[11px] text-zinc-600">
                    Pick a group on the left to post a message.
                  </div>
                )}
              </div>
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

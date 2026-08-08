'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { Send } from 'lucide-react';
import { fetchMyWallUnread } from '@/lib/api';
import { useUnreadMessageCount } from '@/components/platform-messages-bell';

/**
 * Nav entry for the full-page WhatsApp-style chat at `/chat`.
 * Replaces the old slide-over drawer that only showed project walls.
 */
export function FounderChatLauncher() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const dmUnread = useUnreadMessageCount(token);
  const [wallUnread, setWallUnread] = useState(0);

  const loadWallUnread = useCallback(async () => {
    if (!token) {
      setWallUnread(0);
      return;
    }
    try {
      const data = await fetchMyWallUnread(token);
      setWallUnread(data.total);
    } catch {
      /* non-fatal */
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      setWallUnread(0);
      return;
    }
    void loadWallUnread();
    const id = setInterval(() => void loadWallUnread(), 15_000);
    return () => clearInterval(id);
  }, [token, loadWallUnread]);

  const badge = dmUnread + wallUnread;

  if (status === 'loading') {
    return (
      <span className="rounded-lg px-2.5 py-1.5 text-zinc-500" aria-hidden>
        <Send className="h-4 w-4" />
      </span>
    );
  }

  if (!token) {
    return (
      <Link
        href="/login?callbackUrl=/chat"
        className="rounded-lg px-2.5 py-1.5 text-zinc-300 transition hover:bg-zinc-800 hover:text-white"
        title="Sign in for Chat"
        aria-label="Chat — sign in"
      >
        <Send className="h-4 w-4" />
      </Link>
    );
  }

  return (
    <Link
      href="/chat"
      className="relative inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-zinc-200 transition hover:bg-emerald-500/10 hover:text-white"
      title="Open Chat"
      aria-label={badge > 0 ? `Chat, ${badge} unread` : 'Chat'}
    >
      <Send className="h-4 w-4 text-emerald-300" />
      <span className="hidden text-xs font-semibold lg:inline">Chat</span>
      {badge > 0 && (
        <span className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full border border-emerald-400/40 bg-emerald-500 px-1 text-[9px] font-bold leading-none text-black">
          {badge > 9 ? '9+' : badge}
        </span>
      )}
    </Link>
  );
}

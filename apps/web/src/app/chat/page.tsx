'use client';

import { Suspense } from 'react';
import { PlatformChatApp } from '@/components/chat/platform-chat-app';

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#0b141a] text-zinc-400">
          <p className="text-sm">Loading chat…</p>
        </div>
      }
    >
      <PlatformChatApp />
    </Suspense>
  );
}

'use client';

import Link from 'next/link';

/** Required for free AI Agents Directory listing (badge + backlink). */
export function AgentDirectoryBadges({ className = '' }: { className?: string }) {
  return (
    <div className={`flex flex-wrap items-center gap-4 text-xs text-zinc-500 ${className}`}>
      <span className="font-medium uppercase tracking-wider text-zinc-600">Listed on</span>
      <a
        href="https://aiagentsdirectory.com/"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-950/80 px-2.5 py-1.5 text-zinc-400 transition hover:border-violet-500/40 hover:text-violet-200"
        title="Conservative BTC Agent on AI Agents Directory"
      >
        <span aria-hidden className="text-violet-400">
          ◆
        </span>
        AI Agents Directory
      </a>
      <a
        href="https://fushu.dev/"
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-lg border border-zinc-800 bg-zinc-950/80 px-2.5 py-1.5 text-zinc-400 transition hover:text-zinc-200"
      >
        Fushu
      </a>
      <Link href="/docs/signal-api" className="text-violet-400/80 hover:text-violet-300">
        Signal API docs →
      </Link>
    </div>
  );
}

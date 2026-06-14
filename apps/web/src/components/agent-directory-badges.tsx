'use client';

import Link from 'next/link';

/** Protocol discovery links (automated registries only). */
export function AgentDirectoryBadges({ className = '' }: { className?: string }) {
  return (
    <div className={`flex flex-wrap items-center gap-4 text-xs text-zinc-500 ${className}`}>
      <span className="font-medium uppercase tracking-wider text-zinc-600">Discovery</span>
      <a
        href="https://fushu.dev/"
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-lg border border-zinc-800 bg-zinc-950/80 px-2.5 py-1.5 text-zinc-400 transition hover:text-zinc-200"
      >
        Fushu
      </a>
      <a
        href="https://8004scan.io"
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-lg border border-zinc-800 bg-zinc-950/80 px-2.5 py-1.5 text-zinc-400 transition hover:text-zinc-200"
      >
        ERC-8004
      </a>
      <Link href="/docs/signal-api" className="text-violet-400/80 hover:text-violet-300">
        Signal API docs →
      </Link>
    </div>
  );
}

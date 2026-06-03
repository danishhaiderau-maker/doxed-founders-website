'use client';

import { useState } from 'react';

type Props = {
  userId: string;
  hint?: string;
  className?: string;
};

export function UserIdField({ userId, hint, className = '' }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(userId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className={className}>
      <p className="text-xs text-zinc-500">
        {hint ?? 'Share this ID so others can message you on the platform.'}
      </p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
        <code className="flex-1 break-all rounded-lg border border-zinc-700 bg-black px-3 py-2 font-mono text-xs text-cyan-200">
          {userId}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          className="shrink-0 rounded-lg border border-zinc-600 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800"
        >
          {copied ? 'Copied' : 'Copy user ID'}
        </button>
      </div>
    </div>
  );
}

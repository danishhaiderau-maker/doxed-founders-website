'use client';

import { useState } from 'react';

type Props = {
  messagingAddress: string;
  twitterUrl?: string | null;
  hint?: string;
  className?: string;
};

export function PublicMessagingAddress({
  messagingAddress,
  twitterUrl,
  hint,
  className = '',
}: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(messagingAddress);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className={className}>
      <p className="text-xs text-zinc-500">
        {hint ?? 'Share this address so others can find and message you on the platform.'}
      </p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
        <code className="flex-1 break-all rounded-lg border border-cyan-500/30 bg-black px-3 py-2 font-mono text-sm text-cyan-200">
          {messagingAddress}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          className="shrink-0 rounded-lg border border-zinc-600 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800"
        >
          {copied ? 'Copied' : 'Copy address'}
        </button>
      </div>
      {twitterUrl && (
        <p className="mt-2 text-xs text-zinc-500">
          Public X profile:{' '}
          <a href={twitterUrl} target="_blank" rel="noopener noreferrer" className="text-sky-400 hover:underline">
            {twitterUrl.replace(/^https?:\/\//, '')}
          </a>
        </p>
      )}
    </div>
  );
}

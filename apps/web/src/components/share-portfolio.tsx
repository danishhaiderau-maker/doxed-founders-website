'use client';

import { useCallback, useState } from 'react';
import {
  buildPortfolioShareMessage,
  buildPortfolioShareUrl,
  buildTwitterIntentUrl,
} from '@dcf/utils';

interface SharePortfolioProps {
  userId: string;
  displayName: string;
  roi: number;
  totalValue: number;
  compact?: boolean;
}

export function SharePortfolio({
  userId,
  displayName,
  roi,
  totalValue,
  compact = false,
}: SharePortfolioProps) {
  const [copied, setCopied] = useState(false);

  const shareUrl =
    typeof window !== 'undefined'
      ? buildPortfolioShareUrl(window.location.origin, userId)
      : buildPortfolioShareUrl('http://localhost:3000', userId);

  const tweetText = buildPortfolioShareMessage(displayName, roi, totalValue);
  const twitterUrl = buildTwitterIntentUrl(tweetText, shareUrl);

  const copyLink = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt('Copy your portfolio link:', shareUrl);
    }
  }, [shareUrl]);

  if (compact) {
    return (
      <div className="flex flex-wrap gap-2">
        <a
          href={twitterUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-lg bg-sky-500/15 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/25"
        >
          Share on X
        </a>
        <button
          type="button"
          onClick={copyLink}
          className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-muted)] hover:text-white"
        >
          {copied ? 'Copied!' : 'Copy link'}
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
      <h3 className="text-sm font-semibold">Share your portfolio</h3>
      <p className="mt-1 text-xs text-[var(--color-muted)]">
        Post your paper-trading results on X or send the link anywhere.
      </p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          readOnly
          value={shareUrl}
          className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-xs text-zinc-300 outline-none"
        />
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={copyLink}
            className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs font-medium hover:border-[var(--color-accent)]"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <a
            href={twitterUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-sky-500 px-3 py-2 text-xs font-medium text-white hover:bg-sky-400"
          >
            Post on X
          </a>
        </div>
      </div>
    </div>
  );
}

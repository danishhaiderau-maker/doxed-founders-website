'use client';

import { useMemo, useState } from 'react';
import { buildPortfolioShareUrl } from '@dcf/utils';
import type { PositionShareInput } from '@dcf/utils';
import {
  buildPortfolioFlexShare,
  buildPositionFlexShare,
  useShareFlex,
} from '@/components/share-flex-modal';

type SharePortfolioProps = {
  userId: string;
  displayName: string;
  roi: number;
  totalValue: number;
  accessToken?: string;
  compact?: boolean;
};

export function SharePortfolio({
  userId,
  displayName,
  roi,
  totalValue,
  accessToken,
  compact = false,
}: SharePortfolioProps) {
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://doxxedcrypto.digital';

  const flex = useMemo(
    () => buildPortfolioFlexShare({ displayName, roi, totalValue, userId, origin, accessToken }),
    [displayName, roi, totalValue, userId, origin, accessToken],
  );

  const { openFlex, modal } = useShareFlex(flex);
  const shareUrl = buildPortfolioShareUrl(origin, userId);

  if (compact) {
    return (
      <>
        {modal}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={openFlex}
            className="inline-flex items-center gap-1.5 rounded-lg bg-sky-500/15 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/25"
          >
            📜 Share conviction
          </button>
          <CopyLinkButton url={shareUrl} compact />
        </div>
      </>
    );
  }

  return (
    <>
      {modal}
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
        <h3 className="text-sm font-semibold">Share Proof of Conviction</h3>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          Auto-written thread + meme. Sign in with X to post in one tap — no download or paste.
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <input
            readOnly
            value={shareUrl}
            className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-xs text-zinc-300 outline-none"
          />
          <div className="flex shrink-0 gap-2">
            <CopyLinkButton url={shareUrl} />
            <button
              type="button"
              onClick={openFlex}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-white ${
                roi >= 0 ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'
              }`}
            >
              📜 Share conviction
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

type SharePositionProps = PositionShareInput & {
  userId: string;
  projectId: string;
  accessToken?: string;
};

export function SharePosition(props: SharePositionProps) {
  const { userId, projectId, pnlPercent, accessToken } = props;
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://doxxedcrypto.digital';

  const flex = useMemo(
    () => buildPositionFlexShare({ ...props, userId, origin, projectId, accessToken }),
    [props, userId, origin, projectId, accessToken],
  );

  const { openFlex, modal } = useShareFlex(flex);
  const win = pnlPercent >= 0;

  return (
    <>
      {modal}
      <button
        type="button"
        onClick={openFlex}
        className={`rounded-md px-3 py-1.5 text-xs font-medium text-white ${
          win ? 'bg-emerald-600/90 hover:bg-emerald-600' : 'bg-red-600/90 hover:bg-red-600'
        }`}
      >
        📜 Share
      </button>
    </>
  );
}

function CopyLinkButton({ url, compact = false }: { url: string; compact?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt('Copy link:', url);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={
        compact
          ? 'rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-muted)] hover:text-white'
          : 'rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs font-medium hover:border-[var(--color-accent)]'
      }
    >
      {copied ? 'Copied!' : compact ? 'Copy link' : 'Copy'}
    </button>
  );
}

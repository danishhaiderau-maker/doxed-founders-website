'use client';

import { useState } from 'react';
import { SharePreviewModal } from './share-preview-modal';

type Props = {
  text: string;
  url?: string;
  label?: string;
  className?: string;
  stopPropagation?: boolean;
  /** Optional project context passed to the AI paraphrase endpoint. */
  projectName?: string;
  ticker?: string;
  slug?: string;
  /** Auth token — when missing, the AI paraphrase button shows a sign-in hint. */
  accessToken?: string;
};

/**
 * Share button that opens a preview modal (editable text + AI paraphrase +
 * Share on X) instead of jumping straight to twitter.com/intent/tweet.
 *
 * Used by the project page share flow. The generic `ShareOnXButton` stays
 * direct-intent for other surfaces.
 */
export function ShareOnXPreviewButton({
  text,
  url,
  label = 'Share',
  className = '',
  stopPropagation = false,
  projectName,
  ticker,
  slug,
  accessToken,
}: Props) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          if (stopPropagation) e.stopPropagation();
          setOpen(true);
        }}
        className={`inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-950/30 px-2.5 py-1 text-xs font-medium text-sky-200 transition hover:border-sky-400/60 hover:bg-sky-950/50 ${className}`}
        aria-label={label}
      >
        <span aria-hidden>𝕏</span>
        {label}
      </button>

      <SharePreviewModal
        open={open}
        onClose={() => setOpen(false)}
        text={text}
        url={url}
        projectName={projectName}
        ticker={ticker}
        slug={slug}
        accessToken={accessToken}
      />
    </>
  );
}

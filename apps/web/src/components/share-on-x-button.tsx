'use client';

import { buildTwitterIntentUrl } from '@dcf/utils';

type Props = {
  text: string;
  url?: string;
  label?: string;
  className?: string;
  stopPropagation?: boolean;
};

export function ShareOnXButton({
  text,
  url,
  label = 'Share on X',
  className = '',
  stopPropagation = false,
}: Props) {
  const href = buildTwitterIntentUrl(text, url);

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => {
        if (stopPropagation) e.stopPropagation();
      }}
      className={`inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-950/30 px-2.5 py-1 text-xs font-medium text-sky-200 transition hover:border-sky-400/60 hover:bg-sky-950/50 ${className}`}
      aria-label={label}
    >
      <span aria-hidden>𝕏</span>
      {label}
    </a>
  );
}

export function useShareOrigin() {
  if (typeof window === 'undefined') return 'https://doxxedcrypto.digital';
  return window.location.origin;
}

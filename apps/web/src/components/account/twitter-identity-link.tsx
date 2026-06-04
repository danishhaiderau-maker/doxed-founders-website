'use client';

import { normalizeTwitterHandle } from '@dcf/utils';

type Props = {
  handle?: string | null;
  url?: string | null;
  className?: string;
  showLabel?: boolean;
};

export function TwitterIdentityLink({ handle, url, className = '', showLabel = true }: Props) {
  const normalized = normalizeTwitterHandle(handle);
  const href =
    url?.trim() ||
    (normalized ? `https://x.com/${normalized}` : null);

  if (!href || !normalized) {
    return (
      <p className={`text-sm text-zinc-500 ${className}`}>
        No X account linked yet. Connect under Connected Accounts.
      </p>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center gap-1.5 text-sm font-semibold text-sky-300 hover:text-sky-200 hover:underline ${className}`}
    >
      <span aria-hidden>𝕏</span>
      {showLabel && <>@{normalized}</>}
    </a>
  );
}

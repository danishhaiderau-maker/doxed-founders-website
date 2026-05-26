'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import {
  addToWatchlist,
  fetchWatchlistSlugs,
  removeFromWatchlist,
} from '@/lib/api';

interface WatchlistButtonProps {
  slug: string;
  className?: string;
}

export function WatchlistButton({ slug, className = '' }: WatchlistButtonProps) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token) {
      setSaved(false);
      return;
    }
    fetchWatchlistSlugs(token)
      .then((data) => setSaved(data.slugs.includes(slug)))
      .catch(() => setSaved(false));
  }, [token, slug]);

  const toggle = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      if (saved) {
        await removeFromWatchlist(slug, token);
        setSaved(false);
      } else {
        await addToWatchlist(slug, token);
        setSaved(true);
      }
    } finally {
      setLoading(false);
    }
  }, [token, slug, saved]);

  if (!session) {
    return (
      <Link
        href={`/login?callbackUrl=/project/${slug}`}
        className={`rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-muted)] hover:text-white ${className}`}
      >
        Sign in to save
      </Link>
    );
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={loading}
      className={`rounded-lg border px-4 py-2 text-sm font-medium transition disabled:opacity-50 ${
        saved
          ? 'border-amber-500/50 bg-amber-950/30 text-amber-200 hover:border-amber-400'
          : 'border-[var(--color-border)] text-[var(--color-muted)] hover:border-[var(--color-accent)] hover:text-white'
      } ${className}`}
    >
      {loading ? '…' : saved ? '★ Saved' : '☆ Save to watchlist'}
    </button>
  );
}

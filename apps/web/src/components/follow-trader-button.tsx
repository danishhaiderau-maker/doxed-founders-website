'use client';

import { useState } from 'react';
import { followUser, unfollowUser } from '@/lib/api';

type Props = {
  userId: string;
  token?: string | null;
  initiallyFollowing?: boolean;
  size?: 'sm' | 'md';
  onChange?: (following: boolean) => void;
};

export function FollowTraderButton({
  userId,
  token,
  initiallyFollowing = false,
  size = 'sm',
  onChange,
}: Props) {
  const [following, setFollowing] = useState(initiallyFollowing);
  const [loading, setLoading] = useState(false);

  if (!token) return null;

  async function toggle() {
    if (!token) return;
    setLoading(true);
    try {
      if (following) {
        await unfollowUser(userId, token);
        setFollowing(false);
        onChange?.(false);
      } else {
        await followUser(userId, token);
        setFollowing(true);
        onChange?.(true);
      }
    } finally {
      setLoading(false);
    }
  }

  const pad = size === 'md' ? 'px-3 py-1.5 text-sm' : 'px-2 py-1 text-xs';

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={loading}
      className={`rounded-lg font-medium transition disabled:opacity-50 ${
        following
          ? 'border border-zinc-600 text-zinc-300 hover:border-zinc-500'
          : 'bg-emerald-600 text-white hover:bg-emerald-500'
      } ${pad}`}
    >
      {loading ? '…' : following ? 'Following' : 'Follow'}
    </button>
  );
}

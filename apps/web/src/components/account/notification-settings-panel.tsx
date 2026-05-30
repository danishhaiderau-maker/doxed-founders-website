'use client';

import type { NotificationPreferenceGroups } from '@dcf/utils';
import { useCallback, useEffect, useState } from 'react';
import {
  fetchNotificationPreferences,
  updateNotificationPreferences,
} from '@/lib/api';

type PrefGroup = keyof NotificationPreferenceGroups;

const GROUPS: {
  key: PrefGroup;
  title: string;
  description: string;
  fields: { key: string; label: string }[];
}[] = [
  {
    key: 'feed',
    title: 'Feed Alerts',
    description: 'Build updates, deployments, and new projects.',
    fields: [
      { key: 'buildUpdates', label: 'Build updates' },
      { key: 'founderUpdates', label: 'Founder updates' },
      { key: 'newProjects', label: 'New projects' },
      { key: 'deployments', label: 'Deployments' },
    ],
  },
  {
    key: 'market',
    title: 'Market Alerts',
    description: 'Threshold-based market signals — not every trade.',
    fields: [
      { key: 'hotBuys', label: 'Hot buys (≥2% of active traders)' },
      { key: 'hotSells', label: 'Hot sells' },
      { key: 'watchlistSurges', label: 'Watchlist surges' },
    ],
  },
  {
    key: 'scoutVote',
    title: 'Scout Vote Alerts',
    description: 'Community listing votes.',
    fields: [
      { key: 'newVoteOpened', label: 'New vote opened' },
      { key: 'voteEndingSoon', label: 'Vote ending soon' },
      { key: 'voteResult', label: 'Vote result' },
    ],
  },
  {
    key: 'raiseRoom',
    title: 'Raise Room Alerts',
    description: 'Simulated raise milestones.',
    fields: [
      { key: 'newRaise', label: 'New raise' },
      { key: 'milestoneReached', label: 'Milestone reached' },
      { key: 'raiseClosed', label: 'Raise closed' },
    ],
  },
  {
    key: 'following',
    title: 'Following Alerts',
    description: 'When traders, founders, or projects you follow act.',
    fields: [
      { key: 'followedFounderPosted', label: 'Followed founder posted' },
      { key: 'followedTraderBought', label: 'Followed trader bought (≥$100)' },
      { key: 'followedProjectUpdated', label: 'Followed project updated' },
    ],
  },
  {
    key: 'social',
    title: 'Social Alerts',
    description: 'Mentions, replies, and community interactions.',
    fields: [
      { key: 'mentions', label: 'Mentions' },
      { key: 'replies', label: 'Replies' },
      { key: 'follows', label: 'Follows' },
      { key: 'likes', label: 'Likes' },
      { key: 'helpfulMarks', label: 'Helpful marks' },
    ],
  },
  {
    key: 'platform',
    title: 'Platform Alerts',
    description: 'Rewards, rank changes, and system messages.',
    fields: [
      { key: 'rewards', label: 'Rewards' },
      { key: 'rankChanges', label: 'Rank changes' },
      { key: 'systemMessages', label: 'System messages' },
    ],
  },
];

export function NotificationSettingsPanel({ accessToken }: { accessToken: string }) {
  const [prefs, setPrefs] = useState<NotificationPreferenceGroups | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setPrefs(await fetchNotificationPreferences(accessToken));
  }, [accessToken]);

  useEffect(() => {
    load().catch(() => setPrefs(null));
  }, [load]);

  async function toggle(group: PrefGroup, field: string, value: boolean) {
    if (!prefs) return;
    setSaving(true);
    setMsg(null);
    const next = {
      ...prefs,
      [group]: { ...prefs[group], [field]: value },
    };
    setPrefs(next);
    try {
      await updateNotificationPreferences(next, accessToken);
      setMsg('Preferences saved');
    } catch {
      setMsg('Could not save preferences');
      await load();
    } finally {
      setSaving(false);
    }
  }

  if (!prefs) {
    return <p className="text-sm text-zinc-500">Loading notification settings…</p>;
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-zinc-400">
        Control which alerts reach your inbox. Platform-wide alerts only fire after smart thresholds
        are met — e.g. hot buys when ≥2% of active traders buy the same asset.
      </p>
      {msg && <p className="text-sm text-emerald-300">{msg}</p>}
      {GROUPS.map((group) => (
        <section
          key={group.key}
          className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5"
        >
          <h3 className="font-semibold text-white">{group.title}</h3>
          <p className="mt-1 text-xs text-zinc-500">{group.description}</p>
          <ul className="mt-4 space-y-3">
            {group.fields.map((field) => {
              const checked = Boolean(
                (prefs[group.key] as Record<string, boolean>)[field.key],
              );
              return (
                <li key={field.key} className="flex items-center justify-between gap-4">
                  <span className="text-sm text-zinc-300">{field.label}</span>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => toggle(group.key, field.key, !checked)}
                    className={`relative h-6 w-11 rounded-full transition ${
                      checked ? 'bg-emerald-500' : 'bg-zinc-700'
                    }`}
                    aria-pressed={checked}
                  >
                    <span
                      className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition ${
                        checked ? 'left-5' : 'left-0.5'
                      }`}
                    />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

'use client';

import { FEED_HUB_CATEGORIES, type UnifiedFeedCategory } from '@dcf/utils';

type Props = {
  active: UnifiedFeedCategory;
  onChange: (category: UnifiedFeedCategory) => void;
};

export function FeedHubCategoryTabs({ active, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-2">
      {FEED_HUB_CATEGORIES.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onChange(c.id)}
          className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
            active === c.id
              ? 'bg-amber-500/25 text-amber-100 ring-1 ring-amber-500/40'
              : 'border border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-white'
          }`}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

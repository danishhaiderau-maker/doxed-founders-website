'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import {
  countActionableQueueItems,
  FOUNDER_QUEUE_BUCKET_LABELS,
  FOUNDER_QUEUE_BUCKET_ORDER,
  groupFounderQueueByBucket,
  type FounderQueueItem,
} from '@dcf/utils';

type Props = {
  queue: FounderQueueItem[];
  onPrompt?: (prompt: string) => void;
  onQueueAction?: (itemId: string) => Promise<{ message: string } | void>;
  loading?: boolean;
};

function isControlAction(item: FounderQueueItem) {
  return (
    item.action === 'publish' ||
    item.action === 'dispatch_build' ||
    item.action === 'merge_pr' ||
    item.action === 'sync'
  );
}

export function FounderCommandCenterPanels({
  queue,
  onPrompt,
  onQueueAction,
  loading,
}: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const actionableCount = useMemo(() => countActionableQueueItems(queue), [queue]);
  const buckets = useMemo(() => groupFounderQueueByBucket(queue), [queue]);

  async function runControlAction(item: FounderQueueItem) {
    if (!onQueueAction) return;
    setBusyId(item.id);
    setActionNote(null);
    try {
      const result = await onQueueAction(item.id);
      if (result?.message) setActionNote(result.message);
    } catch (err) {
      setActionNote(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusyId(null);
    }
  }

  const hasAny = FOUNDER_QUEUE_BUCKET_ORDER.some((b) => buckets[b].length > 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-4">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          CEO inbox
          {actionableCount > 0 && (
            <span className="ml-2 text-violet-400">Needs you ({actionableCount})</span>
          )}
        </p>
        {actionNote && (
          <p className="mt-2 rounded-lg border border-emerald-500/20 bg-emerald-950/20 px-2 py-1.5 text-[11px] text-emerald-200/90">
            {actionNote}
          </p>
        )}
        {loading && !hasAny ? (
          <p className="mt-3 text-xs text-zinc-600">Loading…</p>
        ) : !hasAny ? (
          <p className="mt-3 text-xs text-zinc-500">
            Inbox is clear. Ask Founder Brain what to ship next.
          </p>
        ) : (
          <div className="mt-3 space-y-4">
            {FOUNDER_QUEUE_BUCKET_ORDER.map((bucketKey) => {
              const items = buckets[bucketKey].slice(0, 4);
              if (items.length === 0) return null;
              return (
                <div key={bucketKey}>
                  <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-600">
                    {FOUNDER_QUEUE_BUCKET_LABELS[bucketKey]}
                    <span className="ml-1 text-zinc-700">({buckets[bucketKey].length})</span>
                  </p>
                  <ul className="mt-2 space-y-2">
                    {items.map((item) => (
                      <li key={item.id}>
                        <QueueRow
                          item={item}
                          onPrompt={onPrompt}
                          onQueueAction={onQueueAction ? () => runControlAction(item) : undefined}
                          busy={busyId === item.id}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function QueueRow({
  item,
  onPrompt,
  onQueueAction,
  busy,
}: {
  item: FounderQueueItem;
  onPrompt?: (prompt: string) => void;
  onQueueAction?: () => void;
  busy?: boolean;
}) {
  const actionLabel =
    item.action === 'open_url'
      ? 'Open'
      : item.action === 'publish'
        ? busy
          ? 'Publishing…'
          : 'Publish'
        : item.action === 'dispatch_build'
          ? busy
            ? 'Running…'
            : 'Build'
          : item.action === 'merge_pr'
            ? busy
              ? 'Merging…'
              : 'Merge'
            : item.action === 'sync'
              ? busy
                ? 'Syncing…'
                : 'Sync'
              : item.action === 'settings'
                ? 'Settings'
                : 'Run';

  const content = (
    <div className="min-w-0 flex-1">
      <p className="truncate text-xs font-medium text-zinc-100">{item.title}</p>
      {item.detail && <p className="truncate text-[10px] text-zinc-600">{item.detail}</p>}
    </div>
  );

  const rowClass =
    'flex w-full items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2 text-left transition hover:border-violet-500/30 disabled:opacity-50';

  if (isControlAction(item) && onQueueAction) {
    return (
      <button type="button" disabled={busy} onClick={() => void onQueueAction()} className={rowClass}>
        {content}
        <span className="shrink-0 text-[10px] font-semibold text-violet-400">{actionLabel}</span>
      </button>
    );
  }

  if (item.prompt && onPrompt && item.action !== 'publish' && item.action !== 'sync') {
    return (
      <button
        type="button"
        onClick={() => onPrompt(item.prompt!)}
        className="flex w-full items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2 text-left transition hover:border-violet-500/30"
      >
        {content}
        <span className="shrink-0 text-[10px] font-semibold text-violet-400">{actionLabel}</span>
      </button>
    );
  }

  if (item.href) {
    const href = item.href;
    const externalOnly = item.action !== 'merge_pr' && item.action !== 'publish';
    if (href.startsWith('http') && externalOnly) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2">
          {content}
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 text-[10px] font-semibold text-zinc-500 hover:text-violet-400"
          >
            View ↗
          </a>
          {isControlAction(item) && onQueueAction && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void onQueueAction()}
              className="shrink-0 text-[10px] font-semibold text-violet-400"
            >
              {actionLabel}
            </button>
          )}
        </div>
      );
    }
    if (href.startsWith('http')) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2 transition hover:border-violet-500/30"
        >
          {content}
          <span className="shrink-0 text-[10px] font-semibold text-violet-400">{actionLabel}</span>
        </a>
      );
    }
    return (
      <Link
        href={href}
        className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2 transition hover:border-violet-500/30"
      >
        {content}
        <span className="shrink-0 text-[10px] font-semibold text-violet-400">{actionLabel}</span>
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2">
      {content}
    </div>
  );
}

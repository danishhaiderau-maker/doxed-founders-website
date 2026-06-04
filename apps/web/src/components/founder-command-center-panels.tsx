'use client';

import Link from 'next/link';
import { useState } from 'react';
import type { AttentionItem, FounderQueueItem } from '@dcf/utils';

type Props = {
  queue: FounderQueueItem[];
  attention: AttentionItem[];
  urgentCount: number;
  onPrompt?: (prompt: string) => void;
  onQueueAction?: (itemId: string) => Promise<{ message: string } | void>;
  loading?: boolean;
};

function severityClass(severity: AttentionItem['severity']) {
  if (severity === 'urgent') return 'border-amber-500/40 bg-amber-950/20';
  if (severity === 'normal') return 'border-violet-500/25 bg-violet-950/15';
  return 'border-zinc-800/80 bg-zinc-900/40';
}

export function FounderCommandCenterPanels({
  queue,
  attention,
  urgentCount,
  onPrompt,
  onQueueAction,
  loading,
}: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const topAttention = attention.slice(0, 5);
  const topQueue = queue.slice(0, 6);

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

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-2xl border border-amber-500/20 bg-zinc-950/60 p-4">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-300/90">
            Attention center
          </p>
          {urgentCount > 0 && (
            <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-semibold text-amber-200">
              {urgentCount} urgent
            </span>
          )}
        </div>
        {loading && !topAttention.length ? (
          <p className="mt-3 text-xs text-zinc-600">Loading…</p>
        ) : topAttention.length === 0 ? (
          <p className="mt-3 text-xs text-zinc-500">Nothing needs you right now — keep building.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {topAttention.map((item) => (
              <li key={item.id}>
                <AttentionRow
                  item={item}
                  queueItem={queue.find((q) => item.id === `att-${q.id}`)}
                  onPrompt={onPrompt}
                  onQueueAction={onQueueAction ? runControlAction : undefined}
                  busy={busyId != null}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-4">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Founder queue
          {queue.length > 0 && (
            <span className="ml-2 text-violet-400">Needs you ({queue.length})</span>
          )}
        </p>
        {actionNote && (
          <p className="mt-2 rounded-lg border border-emerald-500/20 bg-emerald-950/20 px-2 py-1.5 text-[11px] text-emerald-200/90">
            {actionNote}
          </p>
        )}
        {loading && !topQueue.length ? (
          <p className="mt-3 text-xs text-zinc-600">Loading…</p>
        ) : topQueue.length === 0 ? (
          <p className="mt-3 text-xs text-zinc-500">Queue is clear. Ask Founder Brain what to ship next.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {topQueue.map((item) => (
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
        )}
      </div>
    </div>
  );
}

function isControlAction(item: FounderQueueItem) {
  return (
    item.action === 'publish' ||
    item.action === 'dispatch_build' ||
    item.action === 'sync'
  );
}

function AttentionRow({
  item,
  queueItem,
  onPrompt,
  onQueueAction,
  busy,
}: {
  item: AttentionItem;
  queueItem?: FounderQueueItem;
  onPrompt?: (prompt: string) => void;
  onQueueAction?: (item: FounderQueueItem) => void;
  busy?: boolean;
}) {
  if (queueItem && isControlAction(queueItem) && onQueueAction) {
    return (
      <QueueRow
        item={queueItem}
        onPrompt={onPrompt}
        onQueueAction={() => onQueueAction(queueItem)}
        busy={busy}
        variant="attention"
        severity={item.severity}
      />
    );
  }

  const inner = (
    <>
      <span className="shrink-0 text-[10px] font-bold uppercase text-violet-300">{item.verb}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-zinc-200">{item.title}</span>
    </>
  );

  if (item.prompt && onPrompt) {
    return (
      <button
        type="button"
        onClick={() => onPrompt(item.prompt!)}
        className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition hover:border-violet-500/40 ${severityClass(item.severity)}`}
      >
        {inner}
      </button>
    );
  }

  if (item.href) {
    const href = item.href;
    if (href.startsWith('http')) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className={`flex items-center gap-2 rounded-lg border px-3 py-2 transition hover:border-violet-500/40 ${severityClass(item.severity)}`}
        >
          {inner}
        </a>
      );
    }
    return (
      <Link
        href={href}
        className={`flex items-center gap-2 rounded-lg border px-3 py-2 transition hover:border-violet-500/40 ${severityClass(item.severity)}`}
      >
        {inner}
      </Link>
    );
  }

  return (
    <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${severityClass(item.severity)}`}>
      {inner}
    </div>
  );
}

function QueueRow({
  item,
  onPrompt,
  onQueueAction,
  busy,
  variant = 'queue',
  severity = 'normal',
}: {
  item: FounderQueueItem;
  onPrompt?: (prompt: string) => void;
  onQueueAction?: () => void;
  busy?: boolean;
  variant?: 'queue' | 'attention';
  severity?: AttentionItem['severity'];
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
    variant === 'attention'
      ? `flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition hover:border-violet-500/40 disabled:opacity-50 ${severityClass(severity)}`
      : 'flex w-full items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-950/50 px-3 py-2 text-left transition hover:border-violet-500/30 disabled:opacity-50';

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

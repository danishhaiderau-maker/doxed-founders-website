'use client';

import { useEffect, useSyncExternalStore } from 'react';

/**
 * Founder Event Bus
 *
 * A single in-memory pub/sub that every workspace widget can emit into and
 * subscribe to. The point is: no fake animations. Every UI change corresponds
 * to a REAL event emitted by something that actually happened — an AI request
 * lifecycle step, a git commit landing, a deploy state change, a file opening,
 * a terminal line, an agent starting/stopping, an attachment uploading.
 *
 * The bus keeps a rolling buffer of the last N events so new subscribers (e.g.
 * a freshly opened Agent Event Timeline panel) immediately see recent history.
 */

export type FounderEventCategory =
  | 'AI'
  | 'GIT'
  | 'DEPLOY'
  | 'AGENT'
  | 'FILE'
  | 'TERMINAL'
  | 'CURSOR'
  | 'GITHUB'
  | 'BUILD'
  | 'DATABASE'
  | 'DOCKER'
  | 'OLLAMA'
  | 'VOICE'
  | 'VAULT'
  | 'SYSTEM';

export type FounderEvent = {
  id: string;
  ts: number;
  category: FounderEventCategory;
  kind: string;
  message: string;
  level: 'info' | 'success' | 'warn' | 'error';
  /** Optional grouping key so a panel can show per-agent / per-stream timelines. */
  stream?: string;
  /** Optional numeric progress 0..1 for stages that support it. */
  progress?: number;
  meta?: Record<string, unknown>;
};

type Listener = (events: FounderEvent[]) => void;

const MAX_BUFFER = 200;

let buffer: FounderEvent[] = [];
const listeners = new Set<Listener>();
let seq = 0;

function nextId(): string {
  seq += 1;
  return `evt_${Date.now().toString(36)}_${seq}`;
}

function notify() {
  const snapshot = buffer;
  for (const l of listeners) l(snapshot);
}

export function emitEvent(
  category: FounderEventCategory,
  kind: string,
  message: string,
  opts: {
    level?: FounderEvent['level'];
    stream?: string;
    progress?: number;
    meta?: Record<string, unknown>;
  } = {},
): FounderEvent {
  const ev: FounderEvent = {
    id: nextId(),
    ts: Date.now(),
    category,
    kind,
    message,
    level: opts.level ?? 'info',
    stream: opts.stream,
    progress: opts.progress,
    meta: opts.meta,
  };
  buffer = [...buffer, ev].slice(-MAX_BUFFER);
  notify();
  return ev;
}

/** Replace progress on the most recent event in a stream (used to advance a stage). */
export function advanceStream(stream: string, progress: number, message?: string) {
  const idx = [...buffer].reverse().findIndex((e) => e.stream === stream);
  if (idx === -1) return;
  const realIdx = buffer.length - 1 - idx;
  const updated = { ...buffer[realIdx], progress, ...(message ? { message } : {}) };
  buffer = [...buffer.slice(0, realIdx), updated, ...buffer.slice(realIdx + 1)];
  notify();
}

export function clearStream(stream: string) {
  buffer = buffer.filter((e) => e.stream !== stream);
  notify();
}

export function getEvents(): FounderEvent[] {
  return buffer;
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  // Immediately send current buffer to new subscribers.
  listener(buffer);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * React hook: live stream of founder events (newest last). Components call this
 * and re-render whenever the bus emits.
 */
export function useFounderEvents(filter?: {
  category?: FounderEventCategory;
  stream?: string;
}): FounderEvent[] {
  const filtered = useSyncExternalStore(
    subscribe,
    getEvents,
    getEvents,
  );
  return filtered.filter(
    (e) =>
      (!filter?.category || e.category === filter.category) &&
      (!filter?.stream || e.stream === filter.stream),
  );
}

/** Convenience: subscribe once and tear down (for non-React emitters that want a one-shot). */
export function onFounderEvent(fn: (e: FounderEvent) => void): () => void {
  const wrapped: Listener = (events) => fn(events[events.length - 1]);
  listeners.add(wrapped);
  return () => {
    listeners.delete(wrapped);
  };
}

/** Test/helper: reset the bus (used by error boundary recovery). */
export function resetFounderEventBus() {
  buffer = [];
  notify();
}

export function useEmitOnMount(category: FounderEventCategory, kind: string, message: string, deps: unknown[]) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    emitEvent(category, kind, message);
  }, deps);
}

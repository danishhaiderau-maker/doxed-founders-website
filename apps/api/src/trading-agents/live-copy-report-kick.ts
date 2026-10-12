/** LIVE-FIX-0500 / LIVE-FIX-1054: in-process nudge so the Fly reporter sends
 * fills/stops within ~0.2 s instead of waiting for its 5 s poll. Every kick is
 * honoured (the old version dropped kicks for 3 s after the first one, so a
 * fill/stop written right after ORDER_PLACED waited for the next sweep).
 * Kicks are coalesced into one run 100 ms later plus two safety follow-ups.
 * Best effort, never throws. */
let listener: (() => void) | null = null;
const scheduled = new Map<number, ReturnType<typeof setTimeout>>();

export function setLiveCopyReportKickListener(fn: (() => void) | null): void {
  listener = fn;
  if (!fn) {
    for (const t of scheduled.values()) clearTimeout(t);
    scheduled.clear();
  }
}

/** Schedule reporter runs shortly after an execution event is being written. */
export function kickLiveCopyReporter(delaysMs: number[] = [100, 1000, 3000]): number {
  if (!listener) return 0;
  let added = 0;
  const now = Date.now();
  for (const d of delaysMs) {
    const due = now + d;
    // Coalesce: an already-scheduled run within 15 ms of this one covers it.
    if ([...scheduled.keys()].some((k) => Math.abs(k - due) < 15)) continue;
    const t = setTimeout(() => {
      scheduled.delete(due);
      try { listener?.(); } catch { /* best effort */ }
    }, d);
    (t as { unref?: () => void }).unref?.();
    scheduled.set(due, t);
    added += 1;
  }
  return added;
}

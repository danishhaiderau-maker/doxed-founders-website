/** LIVE-FIX-0500: in-process nudge so the Fly reporter sends fills/stops within
 * ~1 s instead of waiting for its 5 s poll. Best effort, never throws. */
let listener: (() => void) | null = null;
let pending: ReturnType<typeof setTimeout>[] = [];

export function setLiveCopyReportKickListener(fn: (() => void) | null): void {
  listener = fn;
}

/** Schedule reporter runs shortly after an execution event is being written. */
export function kickLiveCopyReporter(delaysMs: number[] = [300, 1200, 3000]): number {
  if (!listener || pending.length) return 0;
  pending = delaysMs.map((d) => setTimeout(() => {
    try { listener?.(); } catch { /* best effort */ }
    pending = pending.slice(1);
  }, d));
  for (const t of pending) (t as { unref?: () => void }).unref?.();
  return pending.length;
}

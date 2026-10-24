/**
 * HM R6 nit 5 (RK 10:26 binding): a PENDING_ENTRY row found at boot is NEVER accepted as "resting". The boot reconcile
 * cancels the resting entry order on Bitfinex and reads back that it is cancelled (gone from active orders AND terminal
 * in order history), reads the order's REAL filled qty (its own trades, cross-checked against order history), and when
 * any of it filled force-flattens reduce-only + cancels every stop and requires the read-back FLAT_NO_STOPS. The row and
 * any fill are stamped non-countable. Any failed read / cancel / stamp / flatten -> not ok (the caller blocks + RED).
 */
import { btcToSats } from '@dcf/utils';

export const BOOT_PENDING_ENTRY_NON_COUNTABLE = 'BOOT_PENDING_ENTRY_CANCELLED';

export type BootPendingRow = { id: string; cycleId: string | null; entryOrderId: number | null; qty: number | null };
export type BootPendingOrderHistory = { status: string; terminal: boolean; filledQty: number } | null;
export type BootPendingOps = {
  cancel: (orderId: number) => Promise<void>;
  activeOrderIds: () => Promise<number[]>;
  orderHistory: (orderId: number) => Promise<BootPendingOrderHistory>;
  /** Sum of |execAmount| of the order's OWN trades. Must THROW when unreadable ([] -> 0 is "read, unfilled"). */
  orderTradesQty: (orderId: number) => Promise<number>;
  /** Reduce-only flatten + cancel every order, then read back. */
  forceFlat: () => Promise<{ verdict: string; errors: string[] }>;
  stampNonCountable: (row: BootPendingRow, info: Record<string, unknown>) => Promise<void>;
};
export type BootPendingRowResult = {
  id: string; entryOrderId: number | null; cancelled: boolean; historyStatus: string | null; filledQty: number | null; stamped: boolean; error: string | null;
};
export type BootPendingCancelResult = {
  ok: boolean; detail: string; rows: BootPendingRowResult[]; flattenRequired: boolean; flattenVerdict: string | null;
};

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e)).slice(0, 160);

export async function bootCancelPendingEntries(rows: BootPendingRow[], ops: BootPendingOps): Promise<BootPendingCancelResult> {
  const out: BootPendingRowResult[] = [];
  const fails: string[] = [];
  for (const row of rows) {
    const r: BootPendingRowResult = { id: row.id, entryOrderId: row.entryOrderId, cancelled: false, historyStatus: null, filledQty: null, stamped: false, error: null };
    out.push(r);
    const fail = (e: string) => { r.error = e; fails.push(`${row.id}:${e}`); };
    const eid = row.entryOrderId;
    if (!(eid != null && Number.isFinite(eid) && eid > 0)) { fail('NO_ENTRY_ORDER_ID'); continue; }
    try { await ops.cancel(eid); } catch (e) { fail(`CANCEL_FAILED:${msg(e)}`); continue; }
    let hist: BootPendingOrderHistory;
    try {
      const active = await ops.activeOrderIds();
      if (active.includes(eid)) { fail('CANCEL_NOT_CONFIRMED:still_active'); continue; }
      hist = await ops.orderHistory(eid);
    } catch (e) { fail(`CANCEL_READBACK_FAILED:${msg(e)}`); continue; }
    if (!hist || !hist.terminal) { fail(`CANCEL_NOT_CONFIRMED:${hist ? hist.status : 'no_history'}`); continue; }
    r.cancelled = true;
    r.historyStatus = hist.status;
    let traded: number;
    try { traded = await ops.orderTradesQty(eid); } catch (e) { fail(`FILL_UNREADABLE:${msg(e)}`); continue; }
    if (!Number.isFinite(traded) || traded < 0 || !Number.isFinite(hist.filledQty) || btcToSats(traded) !== btcToSats(hist.filledQty)) {
      fail(`FILL_UNREADABLE:trades=${traded} history=${hist.filledQty}`); continue;
    }
    r.filledQty = traded;
    try {
      await ops.stampNonCountable(row, {
        reason: BOOT_PENDING_ENTRY_NON_COUNTABLE, live_non_countable: true, live_non_countable_reason: BOOT_PENDING_ENTRY_NON_COUNTABLE,
        entry_order_id: eid, entry_cancel_status: hist.status, entry_filled_qty: traded, flatten_required: btcToSats(traded) > 0,
      });
      r.stamped = true;
    } catch (e) { fail(`STAMP_FAILED:${msg(e)}`); }
  }
  // flatten whenever any fill was read, and also when a fill could not be ruled out (unreadable) — safety first; the
  // boot stays blocked regardless on any failure.
  const flattenRequired = out.some((r) => r.filledQty == null ? r.cancelled : btcToSats(r.filledQty) > 0);
  let flattenVerdict: string | null = null;
  if (flattenRequired) {
    try {
      const f = await ops.forceFlat();
      flattenVerdict = f.verdict;
      if (f.verdict !== 'FLAT_NO_STOPS') fails.push(`FLATTEN:${f.verdict}${f.errors.length ? `:${f.errors.join(',').slice(0, 160)}` : ''}`);
    } catch (e) { flattenVerdict = 'READ_FAILED'; fails.push(`FLATTEN:${msg(e)}`); }
  }
  return { ok: fails.length === 0, detail: fails.length ? fails.join(' | ').slice(0, 500) : `cancelled ${out.length} pending entr${out.length === 1 ? 'y' : 'ies'}`, rows: out, flattenRequired, flattenVerdict };
}

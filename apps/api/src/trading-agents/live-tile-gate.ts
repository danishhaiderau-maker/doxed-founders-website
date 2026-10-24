/**
 * Two-tile live gate, relay side (HM gate N1-N3 / G6; RK 24 Oct 2026: its own PR).
 *
 * Mirror of Fly's services/btc-conservative-agent/live_tile_gate.py (ONE shared helper for every live tile, R4):
 * - stable tile key (= trade-id prefix) -> 7-bit code carried in the Bitfinex entry client order id (cid), so every live
 *   entry order and its fills map back to the tile; reconcile logs decode it;
 * - the per-entry admission checks the relay can prove from the exchange + its own ledger at placement time:
 *   one live position account-wide (LIVE_SKIPPED_MUTEX) and net position 0 before the entry (ACCOUNT_NOT_FLAT).
 * A Fly contract test (test_live_tile_gate.py) fails if the code table drifts from the Python table.
 * Protective-stop cids are NOT tile-keyed (stops are attributed via their owning participant).
 */

/** NEVER renumber or reuse a code; a new tile takes the next free one. Retired: dsl=6, dsf=7 (reserved forever). */
export const LIVE_TILE_KEY_CODES: Readonly<Record<string, number>> = Object.freeze({
  rnd: 1,
  gs1: 2,
  gb1: 3,
  gb2: 4,
  gs6: 5,
  gf3: 8, // GS-FADE3-TP30 (paper only; arm refused: PAPER_ONLY_TILE_KEYS on Fly)
});
export const RESERVED_LIVE_TILE_KEY_CODES: Readonly<Record<string, number>> = Object.freeze({ dsl: 6, dsf: 7 });

/** cid layout (positive int32): bits 28-30 = 0b101 marker, bits 21-27 = tile code, bits 0-20 = hash. */
export const TILE_CID_MARKER = 0b101;
export const TILE_CID_MARKER_SHIFT = 28;
export const TILE_CID_CODE_SHIFT = 21;
export const TILE_CID_HASH_MASK = (1 << TILE_CID_CODE_SHIFT) - 1;

export const LIVE_SKIPPED_MUTEX = 'LIVE_SKIPPED_MUTEX';
export const ACCOUNT_NOT_FLAT = 'ACCOUNT_NOT_FLAT';
export const UNTAGGED_ORDER_REFUSED = 'UNTAGGED_ORDER_REFUSED';

/** Tile key of a trade id (adopt:/relink: wrappers ignored); null when the prefix has no code. */
export function tileKeyForTradeId(tradeId: string | null | undefined): string | null {
  const tid = String(tradeId ?? '').trim().toLowerCase().replace(/^(?:adopt|relink):/, '');
  const i = tid.indexOf('-');
  if (i <= 0 || i === tid.length - 1) return null;
  const head = tid.slice(0, i);
  return Object.prototype.hasOwnProperty.call(LIVE_TILE_KEY_CODES, head) ? head : null;
}

export function tileKeyCode(tileKey: string | null | undefined): number | null {
  const k = String(tileKey ?? '').toLowerCase();
  return Object.prototype.hasOwnProperty.call(LIVE_TILE_KEY_CODES, k) ? LIVE_TILE_KEY_CODES[k] : null;
}

/** Tile-keyed cid from a legacy 31-bit hash (the hash's low 21 bits keep orders of one tile distinct). */
export function encodeTileClientOrderId(tileKey: string, hash: number): number {
  const code = tileKeyCode(tileKey);
  if (code == null) throw new Error(`unknown live tile key ${tileKey}`);
  return ((TILE_CID_MARKER << TILE_CID_MARKER_SHIFT) | (code << TILE_CID_CODE_SHIFT) | (hash & TILE_CID_HASH_MASK)) >>> 0;
}

/** Tile key carried in a cid; null for legacy / foreign / stop cids. */
export function decodeTileClientOrderId(cid: number | string | null | undefined): string | null {
  const c = Number(cid);
  if (!Number.isInteger(c) || c <= 0 || c > 0x7fffffff) return null;
  if (Math.floor(c / 2 ** TILE_CID_MARKER_SHIFT) !== TILE_CID_MARKER) return null;
  const code = Math.floor(c / 2 ** TILE_CID_CODE_SHIFT) & 0x7f;
  const hit = Object.entries(LIVE_TILE_KEY_CODES).find(([, v]) => v === code);
  return hit ? hit[0] : null;
}

/**
 * Per-entry relay admission (pure). ``otherLiveParticipants``: this account's OTHER PENDING_ENTRY/OPEN participants
 * (any tile); ``positionQty``: the fresh exchange position read just before the entry (null = proven flat / no row).
 */
export function liveEntryAdmission(input: {
  otherLiveParticipants: number;
  positionQty: number | null | undefined;
}): { ok: true } | { ok: false; reason: string } {
  if (Number(input.otherLiveParticipants) > 0) return { ok: false, reason: LIVE_SKIPPED_MUTEX };
  const q = Number(input.positionQty ?? 0);
  if (!Number.isFinite(q) || Math.abs(q) > 0) return { ok: false, reason: ACCOUNT_NOT_FLAT };
  return { ok: true };
}

/**
 * RK 02:25 24 Oct: ONLY reduce-only closing orders may go out without a tile key. Any order that is not reduce-only
 * (every entry: limit, chase replacement, catch-up market) must carry a cid that decodes to a live tile; otherwise it is
 * refused before the exchange write. Returns null when allowed, else UNTAGGED_ORDER_REFUSED. Pure; the same rule is
 * enforced by the Fly guardian on the exchange mirror (UNTAGGED_ORDER / UNTAGGED_POSITION => RED, disarm ALL, force-flat).
 */
export function untaggedOrderRefusal(input: {
  clientOrderId: number | string | null | undefined;
  reduceOnly: boolean;
}): string | null {
  if (input.reduceOnly === true) return null;
  return decodeTileClientOrderId(input.clientOrderId) ? null : UNTAGGED_ORDER_REFUSED;
}

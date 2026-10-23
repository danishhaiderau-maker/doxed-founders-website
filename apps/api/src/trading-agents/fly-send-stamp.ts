import { createHmac, timingSafeEqual } from 'crypto';

/**
 * RK ruling (1), 24 Oct 2026: measured Fly-send stamp for the ONE shared Fly->relay path.
 *
 * The signed relay body is hash-identical across retries (Railway rejects a replay whose sha256 differs), so the
 * per-attempt send instant cannot live in the body. Fly sends it in `X-Fly-Send-Started-At` (epoch seconds) with
 * `X-Fly-Send-Signature: sha256=HMAC(SHOWCASE_WEBHOOK_SECRET, "<event_id>|<ts>")`. Railway keeps it only when the
 * HMAC verifies and the instant is not after Railway's own receive instant (plus clock slack) and not before the
 * signed event `ts` (minus slack). Anything else returns null: the row stays non-countable (R17), nothing else
 * changes (additive only).
 */
export const FLY_SEND_STAMP_CLOCK_SLACK_MS = 2_000;

export function flySendStampMessage(eventId: string, sentAtRaw: string): string {
  return `${eventId}|${sentAtRaw}`;
}

export function verifyFlySendStamp(input: {
  sentAtHeader?: string | null;
  signatureHeader?: string | null;
  eventId?: string | null;
  eventTs?: string | null;
  platformReceivedAtMs: number;
  secret?: string | null;
}): string | null {
  const secret = (input.secret ?? '').trim();
  const raw = (input.sentAtHeader ?? '').trim();
  const eventId = (input.eventId ?? '').trim();
  const sig = /^sha256=([0-9a-fA-F]{64})$/.exec((input.signatureHeader ?? '').trim());
  if (!secret || !raw || !eventId || !sig || !/^\d{9,11}(\.\d{1,9})?$/.test(raw)) return null;
  const expected = createHmac('sha256', secret).update(flySendStampMessage(eventId, raw)).digest();
  const provided = Buffer.from(sig[1], 'hex');
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) return null;
  const sentMs = Number(raw) * 1000;
  if (!Number.isFinite(sentMs) || !Number.isFinite(input.platformReceivedAtMs)) return null;
  if (sentMs > input.platformReceivedAtMs + FLY_SEND_STAMP_CLOCK_SLACK_MS) return null;
  const eventMs = Date.parse(String(input.eventTs ?? ''));
  if (Number.isFinite(eventMs) && sentMs < eventMs - FLY_SEND_STAMP_CLOCK_SLACK_MS) return null;
  return new Date(sentMs).toISOString();
}

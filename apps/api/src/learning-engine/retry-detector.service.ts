import { Injectable, Logger } from '@nestjs/common';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';

/**
 * Retry Detector — Phase 4 outcome-capture kernel service.
 *
 * The single strongest negative signal the kernel can observe passively is:
 * the founder sent the same prompt again within a few seconds. That implies
 * the previous response was bad enough to be worth re-spending DDollars on.
 *
 * This service keeps an in-memory sliding window of recent requests keyed by
 * `userId:promptHash`. When a second request lands for the same key within
 * the retry window (60s), the PREVIOUS RoutingDecision row is marked
 * `retried: true` via the Flight Recorder — that row is the one that lost
 * the founder's confidence.
 *
 * No Redis, no DB writes for the detector itself; just a Map with periodic
 * garbage collection. Restart-safe is not required: a missed retry is a
 * minor signal loss, not a correctness bug.
 */
@Injectable()
export class RetryDetectorService {
  private readonly logger = new Logger(RetryDetectorService.name);

  /** Two requests for the same key within this window count as a retry. */
  static readonly RETRY_WINDOW_MS = 60_000;

  /** Entries older than this are eligible for GC. Keeps the Map bounded. */
  static readonly ENTRY_TTL_MS = 5 * 60_000;

  /** Run GC roughly every N inserts to amortize the cost. */
  static readonly GC_EVERY_N_INSERTS = 64;

  /**
   * Keyed by `${userId}:${promptHash}`. The value is the most recent request
   * for that key — a second hit inside RETRY_WINDOW_MS marks the previous
   * row as retried, then the entry is replaced with the new request.
   */
  private readonly window = new Map<string, RetryEntry>();

  private insertCount = 0;

  constructor(private readonly flightRecorder: FlightRecorderService) {}

  /**
   * Record a request and detect whether it is a retry of a recent request
   * by the same founder with the same prompt hash.
   *
   * Input → Decision → Output:
   *   Input:    requestId + promptHash + userId (+ chosen model/provider
   *             for logging context only).
   *   Decision: is there an unexpired prior entry for this key?
   *   Output:   { isRetry, previousRequestId }. As a side effect, when a
   *             retry is detected the PREVIOUS RoutingDecision row is
   *             patched to `retried: true` (best-effort, fire-and-forget
   *             from the caller's perspective).
   */
  async recordRequest(input: {
    requestId: string;
    promptHash: string;
    userId: string;
    chosenModel: string;
    chosenProvider: string;
  }): Promise<{ isRetry: boolean; previousRequestId?: string }> {
    const key = this.key(input.userId, input.promptHash);
    const now = Date.now();

    // -- Decision step -------------------------------------------------------
    const prior = this.window.get(key);
    const isRetry =
      !!prior && now - prior.timestamp <= RetryDetectorService.RETRY_WINDOW_MS;

    let previousRequestId: string | undefined;
    if (isRetry && prior) {
      previousRequestId = prior.requestId;
      // Mark the PREVIOUS decision as retried. The new request gets its own
      // fresh row from the Routing Engine / Flight Recorder; we do NOT
      // pre-flag it — the *next* duplicate (if any) will do that.
      void this.flightRecorder
        .updateOutcome(prior.requestId, { retried: true })
        .catch((err) =>
          this.logger.warn(
            `updateOutcome(retried) failed for requestId=${prior.requestId}: ${
              err instanceof Error ? err.message : String(err)
            }`,
          ),
        );
    }

    // -- Output step ---------------------------------------------------------
    // Always replace the entry with the latest request so the next duplicate
    // (a triple-send) flags THIS request, not the original.
    this.window.set(key, {
      requestId: input.requestId,
      timestamp: now,
      chosenModel: input.chosenModel,
      chosenProvider: input.chosenProvider,
    });
    this.insertCount += 1;
    if (this.insertCount % RetryDetectorService.GC_EVERY_N_INSERTS === 0) {
      this.gc(now);
    }

    return { isRetry, previousRequestId };
  }

  /** Current window size — used by the admin status endpoint. */
  get trackedPromptHashes(): number {
    return this.window.size;
  }

  /** Drop expired entries. Exposed for tests + the periodic GC above. */
  gc(now = Date.now()): number {
    let removed = 0;
    for (const [key, entry] of this.window) {
      if (now - entry.timestamp > RetryDetectorService.ENTRY_TTL_MS) {
        this.window.delete(key);
        removed += 1;
      }
    }
    return removed;
  }

  private key(userId: string, promptHash: string): string {
    return `${userId}:${promptHash}`;
  }
}

type RetryEntry = {
  requestId: string;
  timestamp: number;
  chosenModel: string;
  chosenProvider: string;
};

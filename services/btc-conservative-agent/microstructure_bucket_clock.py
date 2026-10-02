"""Choose observed seconds without manufacturing a backlog of market ticks."""
import math


def observed_bucket(next_bucket, observed_at):
    observed_at = float(observed_at)
    if not math.isfinite(observed_at):
        raise ValueError("OBSERVATION_TIME_INVALID")
    bucket = math.floor(observed_at)
    next_bucket = int(next_bucket)
    if bucket < next_bucket:
        return {"ready": False, "wait_seconds": max(0.01, next_bucket - observed_at)}
    skipped = bucket - next_bucket
    return {"ready": True, "bucket_ts": bucket, "next_bucket": bucket + 1,
            "observed_at_ts": observed_at, "skipped_bucket_count": skipped,
            "skipped_start_ts": next_bucket if skipped else None,
            "skipped_end_ts_exclusive": bucket if skipped else None,
            "gap_reason": "MISSED_OBSERVATION_NOT_BACKFILLED" if skipped else None}


def trade_interval_proof(bucket, collected_at, retention, start, end):
    retained = (retention.get("started_ts", math.inf) <= bucket
                and retention.get("last_evicted_ts", math.inf) < bucket
                and collected_at >= bucket + 1)
    continuous = (start.get("generation", 0) > 0 and start == end
                  and start.get("connected_ts", math.inf) <= bucket
                  and (start.get("last_disconnect") or 0) <= bucket
                  and start.get("connected") is True and start.get("ready") is True
                  and start.get("trades_subscribed") is True)
    return {"schema": "observed_trade_interval_coverage_v1", "retained_complete": bool(retained),
            "observed_stream_continuous": bool(continuous),
            "complete": bool(retained and continuous),
            "basis": "LOCAL_RECEIVE_STREAM_AND_RETENTION_NOT_EXCHANGE_SEQUENCE_PROOF",
            "last_evicted_ts": retention.get("last_evicted_ts"),
            "connection_generation": start.get("generation")}

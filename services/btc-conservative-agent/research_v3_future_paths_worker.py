"""Low-priority isolated worker for future-path evidence maturation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from research_v3_future_paths import mature_future_market_paths


RESULT_SCHEMA = "all_opportunity_future_path_worker_result_v1"
MAX_RSS_BYTES = 256 * 1024 * 1024
MAX_CPU_SECONDS = 5


def _apply_limits() -> None:
    if hasattr(os, "nice"):
        os.nice(19)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
    except (ImportError, OSError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--now-ts", required=True, type=float)
    parser.add_argument("--max-batch", required=True, type=int)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    try:
        _apply_limits()
        root = Path(args.data_dir).resolve(strict=True)
        result_path = Path(args.result).resolve()
        receipt_root = (root / "v3" / "receipts").resolve()
        receipt_root.mkdir(parents=True, exist_ok=True)
        if result_path.parent != receipt_root or not result_path.name.startswith("future-path-worker-"):
            raise ValueError("FUTURE_PATH_WORKER_RESULT_OUTSIDE_RECEIPTS")
        receipt = mature_future_market_paths(
            data_dir=root, epoch_id=args.epoch_id, now_ts=args.now_ts,
            max_batch=max(1, min(64, args.max_batch)),
        )
        result = {
            "schema": RESULT_SCHEMA,
            **{key: receipt.get(key) for key in (
                "epoch_id", "now_ts", "candidate_count", "pending_count",
                "mature_selected", "complete_count", "unknown_count",
                "source_tape_present", "cursor",
                "terminal_append_dispositions", "terminal_append_authority",
                "terminal_append_attempted_count",
            )},
        }
        temporary = result_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, result_path)
        return 0
    except BaseException:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

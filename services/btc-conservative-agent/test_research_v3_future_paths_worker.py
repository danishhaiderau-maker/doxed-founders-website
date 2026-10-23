import json
import ast
from pathlib import Path
import subprocess
import sys

from research_v3_future_paths import mature_future_market_paths
from research_v3_store import V3EvidenceStore


HERE = Path(__file__).resolve().parent
WORKER = HERE / "research_v3_future_paths_worker.py"
BOT = HERE / "bot.py"


def test_worker_retired_r17_writes_no_segment_and_never_no_fill(tmp_path):
    """R17: market_segments writers are retired. The worker (and the
    maturation it wraps) reports "disabled: R17", writes no segment and never
    classifies an opportunity NO_FILL. bot.main() no longer starts the loop."""
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-worker")
    store.append("opportunity", {
        "record_id": "opportunity:1", "episode_id": "episode:1",
        "signal_ts": 1_700_000_000, "symbol": "BTCUSD",
    })
    direct = mature_future_market_paths(
        data_dir=tmp_path, epoch_id="epoch-worker", now_ts=1_700_009_000, max_batch=8,
    )
    assert direct["status"] == "disabled: R17" and direct["writes"] == []
    result = tmp_path / "v3" / "receipts" / "future-path-worker-test.json"
    completed = subprocess.run([
        sys.executable, str(WORKER), "--data-dir", str(tmp_path),
        "--epoch-id", "epoch-worker", "--now-ts", "1700009000",
        "--max-batch", "64", "--result", str(result),
    ], check=False, timeout=10)
    assert completed.returncode == 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["schema"] == "all_opportunity_future_path_worker_result_v1"
    assert payload["status"] == "disabled: R17"
    assert payload["retired"] == "R17"
    assert payload["unknown_count"] == 0 and payload["complete_count"] == 0
    ledger = tmp_path / "v3" / "ledgers" / "market_segment.jsonl"
    assert not ledger.exists() or "NO_FILL" not in ledger.read_text()
    assert not list((tmp_path / "v3" / "market_segments").glob("*/*.json"))
    bot_source = BOT.read_text(encoding="utf-8")
    assert "FUTURE_PATH_WORKER_RETIRED_R17 = True" in bot_source


def test_worker_has_hard_cpu_memory_and_batch_guards():
    source = WORKER.read_text(encoding="utf-8")
    assert "os.nice(19)" in source
    assert "RLIMIT_AS" in source and "RLIMIT_CPU" in source
    assert "MAX_CPU_SECONDS = 5" in source
    assert "min(64, args.max_batch)" in source
    assert "FUTURE_PATH_WORKER_RESULT_OUTSIDE_RECEIPTS" in source
    bot_source = BOT.read_text(encoding="utf-8")
    loop_start = bot_source.index("def all_opportunity_future_path_evidence_loop")
    loop_end = bot_source.index("_COUNTERFACTUAL_REQUIRED_POLICY_KEYS", loop_start)
    loop = bot_source[loop_start:loop_end]
    assert "subprocess.run(" in loop
    assert "timeout=FUTURE_PATH_WORKER_WALL_TIMEOUT_SEC" in loop
    assert "stdout=subprocess.DEVNULL" in loop


def _literal_assignment(source: str, name: str):
    tree = ast.parse(source)
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
                return ast.literal_eval(statement.value)
    raise AssertionError(f"missing literal assignment: {name}")


def test_parent_wall_timeout_allows_throttled_worker_but_stays_bounded():
    worker_cpu_seconds = _literal_assignment(
        WORKER.read_text(encoding="utf-8"), "MAX_CPU_SECONDS"
    )
    parent_wall_seconds = _literal_assignment(
        BOT.read_text(encoding="utf-8"), "FUTURE_PATH_WORKER_WALL_TIMEOUT_SEC"
    )
    # nice(19) can stretch five seconds of CPU substantially on a shared vCPU.
    # Parent supervision must allow that throttling without becoming unbounded.
    assert parent_wall_seconds >= worker_cpu_seconds * 4
    assert parent_wall_seconds <= 45

import ast
import copy
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from evidence_phase_trace import EvidencePhaseTrace


def test_actual_hook_append_and_health_expose_phases_without_payload(tmp_path):
    names = {"_run_post_ai_evidence_hook", "_safe_append_jsonl", "post_ai_evidence_health_snapshot"}
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    trace = EvidencePhaseTrace()
    ns = {"json": json, "copy": copy, "os": os, "threading": threading, "time": time,
          "_post_ai_evidence_phase_trace": trace, "state_lock": threading.RLock(),
          "_post_ai_evidence_status": {"completed": 0}, "scheduled_ai_cycle_state": {},
          "_post_ai_evidence_workers_lock": threading.RLock(), "_post_ai_evidence_workers": {},
          "_research_write_gate": threading.RLock(), "_jsonl_path_lock": lambda p: threading.RLock(),
          "_jsonl_serialized_append_targets": set(), "CSV_WRITE_RETRIES": 1,
          "emergency_admission": lambda **k: {"allowed": True}, "_data_sync_runtime_root": lambda: tmp_path,
          "_validate_or_quarantine_jsonl": lambda *a: None, "rotate_log": lambda p: None,
          "_persist_jsonl_validation_receipt": lambda *a: None, "_jsonl_validation_signature": lambda p: 1,
          "logger": SimpleNamespace(error=lambda *a: None)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "actual-phase-integration", "exec"), ns)
    def log(ctx, ai, lane):
        assert ns["_safe_append_jsonl"](str(tmp_path / "evidence.jsonl"), {"secret_payload": "DO_NOT_REPORT"})
    ns["log_ai_reason_research"] = log
    ns["_run_post_ai_evidence_hook"]({"key": "PRIVATE_TRADE_ID", "payload": {"hook": "ai_reason"}})
    health = ns["post_ai_evidence_health_snapshot"]()
    phases = health["phase_timing"]["recent"][0]["phase_seconds"]
    assert {"research_gate_wait", "path_lock_wait", "validation", "rotation", "append_write", "file_fsync", "validation_receipt", "completion_lock_wait"} <= set(phases)
    assert health["completed"] == 1
    assert "PRIVATE_TRADE_ID" not in json.dumps(health)
    assert "DO_NOT_REPORT" not in json.dumps(health)
    assert json.loads((tmp_path / "evidence.jsonl").read_text())["secret_payload"] == "DO_NOT_REPORT"

"""Real-run entry point. CPU fallback is not available here.

Parity tests call evaluate_exit_lattice(allow_cpu_fallback=True). This entry
point stops if that flag is set or if CUDA is absent. Import pins and data
file hashes are checked before any lattice work.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.ext2_costs import Ext2Stop
from research.ext2_loader import verify_manifest_shas
from research.ext2_search import _cuda_available, count_combos
from research.ext2_supervisor import (
    assert_roots,
    audit_files,
    guarded_write,
    real_run_device_stop,
    seal_receipt,
    verify_import_pins,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = SERVICE_ROOT / "ci" / "ext2_import_pins.json"
SOURCE_FILES = (
    "ext2_costs.py",
    "ext2_loader.py",
    "ext2_search.py",
    "ext2_supervisor.py",
    "ext2_runner.py",
)


def run_ext2(*, data_root: Path, output_root: Path, manifest: Mapping[str, str] | None = None,
             allowlist: list[str] | None = None, allow_cpu_fallback: bool = False,
             disk_free: int | None = None, pin_path: Path | None = None,
             forbidden_substrings: tuple[str, ...] = ()) -> dict[str, Any]:
    stages: list[dict[str, Any]] = [{"stage": "import_pins", "device": "cpu", "fallback": False}]
    receipt: dict[str, Any] = {
        "schema": "ext2_run_receipt_v1",
        "live": False,
        "paper_only": True,
        "edge_claim": False,
        "status": "STOP",
        "reason": "STOP",
        "combos_searched": None,
        "devices": stages,
        "fallback": False,
    }
    try:
        if allow_cpu_fallback:
            raise Ext2Stop("STOP_CPU_FALLBACK_FORBIDDEN")
        assert_roots(Path(data_root), Path(output_root))
        verify_import_pins(Path(pin_path) if pin_path is not None else PIN_PATH, SERVICE_ROOT)
        violations = audit_files([Path(__file__).resolve().parent / name for name in SOURCE_FILES])
        if violations:
            raise Ext2Stop("STOP_NETWORK")
        stages.append({"stage": "source_audit", "device": "cpu", "fallback": False})
        if manifest is not None:
            verify_manifest_shas(Path(data_root), manifest, allowlist=allowlist)
            stages.append({"stage": "data_sha", "device": "cpu", "fallback": False, "files": len(manifest)})
        counted = count_combos()
        receipt["combos_searched"] = counted
        stages.append({"stage": "count", "device": "cpu", "fallback": False})
        if disk_free is not None and int(disk_free) < 100 * 1024 ** 3:
            raise Ext2Stop("STOP_DISK_FLOOR")
        if not _cuda_available():
            stages.append({"stage": "exit_lattice", "device": None, "fallback": False, "status": "STOP"})
            raise Ext2Stop("STOP_GPU_MISSING")
        stages.append({"stage": "exit_lattice", "device": "gpu", "fallback": False})
        blocked = real_run_device_stop(stages)
        if blocked:
            raise Ext2Stop(blocked)
        receipt["status"] = "READY"
        receipt["reason"] = "GPU_PRESENT"
    except Ext2Stop as exc:
        receipt["status"] = "STOP"
        receipt["reason"] = exc.reason
    receipt["devices"] = stages
    receipt["combos_searched"] = receipt["combos_searched"] if receipt["combos_searched"] is not None else None
    sealed = seal_receipt(receipt, forbidden_substrings)
    guarded_write(
        Path(output_root) / "run_receipt.json",
        json.dumps(sealed, sort_keys=True),
        data_root=Path(data_root),
        output_root=Path(output_root),
    )
    return sealed

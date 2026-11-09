"""EXT2 W1 run entry point (A19 order). Paper only. Live is off.

Modes
  real   FROZEN-v3 calendar only, CUDA required (no CPU fallback: STOP_GPU_MISSING),
         CUDA pre-start check (T2000 + on-device float64 parity) or STOP_CUDA_PRESTART,
         dry-check projection > 2 h = STOP_PROJECTION.
  smoke  synthetic data / small calendars on the numpy or CPU-torch backend. Labelled
         SMOKE-NOT-A-RUN; never a real result.

The worker (``python -m research.ext2_runner``) runs under ext2_supervisor in a run
root the supervisor created exclusively. It reads EXT2_RUN_NONCE / EXT2_CARD_SHA12,
announces each unit in unit.json (the parent times units), writes every output with
mode 'x', writes run_receipt.json, and writes COMPLETE {run_nonce, card_sha12,
finished_aedt} last. A COMPLETE present at start is a STOP.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from research import ext2_pipeline as pipeline
from research import ext2_rules as rules
from research.ext2_calendar import FROZEN, Calendar, is_frozen
from research.ext2_costs import FEE_BP, Ext2Stop
from research.ext2_lattice import NumpyBackend, PrintPath, compute_base, cuda_available, make_backend
from research.ext2_loader import ALLOWLIST_AGGREGATE_SHA, H1_SEAL_SHA, aggregate_manifest_sha, build_grid, \
    load_bitfinex
from research.ext2_search import count_combos
from research.ext2_supervisor import (
    CARD_ENV,
    CARD_SHA_V3,
    COMPLETION_NAME,
    HEARTBEAT_NAME,
    NONCE_ENV,
    RECEIPT_REQUIRED_KEYS,
    UNIT_NAME,
    WORKER_RECEIPT_NAME,
    HeartbeatThread,
    aedt_now,
    assert_roots,
    audit_files,
    guarded_write,
    install_network_audit_hook,
    real_run_device_stop,
    seal_receipt,
    verify_import_pins,
    write_control,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = SERVICE_ROOT / "ci" / "ext2_import_pins.json"
SOURCE_FILES = (
    "ext2_calendar.py",
    "ext2_costs.py",
    "ext2_features.py",
    "ext2_lattice.py",
    "ext2_loader.py",
    "ext2_pipeline.py",
    "ext2_rules.py",
    "ext2_runner.py",
    "ext2_search.py",
    "ext2_supervisor.py",
)
SMOKE_LABEL = "SMOKE-NOT-A-RUN (synthetic or non-frozen data; never a result)"
PARITY_ATOL = 1e-9
assert FEE_BP == 0.0, "fee_bp must be 0 (frozen card)"


class UnitAnnouncer:
    """Writes unit.json so the supervisor can time each CPU/GPU unit on its own clock."""

    def __init__(self, root: Path | None):
        self.root = root
        self.seq = 0
        self.log: list[dict[str, Any]] = []

    def __call__(self, name: str, device: str) -> None:
        self.seq += 1
        entry = {"seq": self.seq, "device": "gpu" if device == "gpu" else "cpu", "name": name}
        self.log.append(entry)
        if self.root is not None:
            write_control(self.root / UNIT_NAME, json.dumps(entry))


def _micro_path(seed: int = 11, n: int = 20_000):
    rng = np.random.default_rng(seed)
    mts = np.cumsum(rng.integers(1, 4_000, n)).astype(np.int64) + 1_700_000_000_000
    price = 60_000.0 * np.exp(np.cumsum(rng.standard_normal(n) * 2e-4))
    pick = rng.integers(0, n - 2_000, 2_000)
    events = {"mts": mts[pick] + 500, "price": price[pick], "side": rng.choice([-1.0, 1.0], 2_000),
              "boundary": np.full(2_000, int(mts[-1]) + 1, dtype=np.int64)}
    return mts, price, events


def lattice_parity(backend, *, seed: int = 11) -> dict[str, Any]:
    """Micro-grid parity of `backend` against the numpy reference (indices exact, matmul <= 1e-9)."""
    mts, price, ev = _micro_path(seed)
    ref = compute_base(PrintPath(mts, price, NumpyBackend()), ev["mts"], ev["price"], ev["side"], ev["boundary"])
    t0 = time.monotonic()
    got = compute_base(PrintPath(mts, price, backend), ev["mts"], ev["price"], ev["side"], ev["boundary"])
    seconds = time.monotonic() - t0
    index_mismatch = sum(int(np.sum(ref[k] != got[k])) for k in ("start", "cens", "liq", "hs", "tp", "ts"))
    rng = np.random.default_rng(seed + 1)
    a = rng.standard_normal((64, 128))
    b = rng.standard_normal((128, 96))
    mm = float(np.max(np.abs(np.asarray(backend.matmul(a, b)) - a @ b)))
    return {"events": int(ev["mts"].size), "index_mismatches": index_mismatch, "matmul_max_abs_diff": mm,
            "dtype": got["dtype"], "kernel": got["kernel"], "device": got["device"],
            "events_per_sec": float(ev["mts"].size / seconds) if seconds > 0 else None,
            "pass": index_mismatch == 0 and mm <= PARITY_ATOL and got["dtype"] == "float64"}


def cuda_prestart(backend, *, run_cmd: Callable[..., Any] | None = None, require_t2000: bool = True) -> dict[str, Any]:
    """Real-run pre-start (A19/B9): nvidia-smi says T2000, torch sees the device,
    on-device float64 micro-grid parity <= 1e-9. Any failure = STOP_CUDA_PRESTART."""
    import subprocess
    run = run_cmd or subprocess.run
    out: dict[str, Any] = {"ok": False}
    try:
        proc = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                   capture_output=True, text=True, timeout=30, check=False)
        smi = (proc.stdout or "").strip()
    except (OSError, ValueError):
        smi = ""
    out["nvidia_smi"] = smi[:200]
    if require_t2000 and "T2000" not in smi:
        out["fail"] = "nvidia_smi_not_t2000"
        return out
    torch = backend.torch
    out["torch_version"] = str(torch.__version__)
    out["torch_device"] = str(torch.cuda.get_device_name(0))
    parity = lattice_parity(backend)
    out["parity"] = parity
    if not parity["pass"]:
        out["fail"] = "parity"
        return out
    out["ok"] = True
    return out


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _projection(ctx, segments_fn, backend, prepare_seconds: float, measured_fp64_on_device: bool) -> dict[str, Any]:
    """Dry-check runtime projection from measured samples (lattice chunk, one segment's
    chains, a White RC slice), scaled linearly. Conservative x32 lattice factor when
    not measured in float64 on the device (N11)."""
    segments = segments_fn()
    total_events = 0
    for seg in segments:
        seg["entries"] = pipeline.segment_entries(ctx, seg)
        total_events += 2 * int(seg["bars"].shape[0])
    trades = ctx["trades"]
    path = PrintPath(trades["mts"], trades["price"], backend)
    seg0 = segments[0]
    m0 = int(seg0["bars"].shape[0])
    sample = min(m0, 32_768)
    ent = seg0["entries"]
    em = np.where(ent["valid"][:sample], ent["entry_mts"][:sample], 0)
    ep = np.where(ent["valid"][:sample], ent["entry_price"][:sample], 1.0)
    t0 = time.monotonic()
    compute_base(path, em, ep, np.ones(sample), np.full(sample, int(seg0["boundary_mts"]), dtype=np.int64))
    lattice_s = (time.monotonic() - t0) * total_events / max(sample, 1)
    base = pipeline.compute_lattice(ctx, [seg0], NumpyBackend())
    t0 = time.monotonic()
    pipeline.run_chains(ctx, seg0, base, None, acc=pipeline.Accumulator(len(ctx["calendar"].test_days()),
                                                                        len(ctx["calendar"].folds)))
    seg_s = time.monotonic() - t0
    total_bars = sum(int(seg["bars"].shape[0]) for seg in segments)
    chain_s = seg_s * total_bars / max(m0, 1)
    days = max(len(ctx["calendar"].test_days()), 2)
    rows = 5_000
    panel = np.random.default_rng(3).standard_normal((rows, days))
    t0 = time.monotonic()
    rules.white_rc_pvalues(panel, np.ones(rows, dtype=bool), draws=200)
    rc_s = (time.monotonic() - t0) * (pipeline.N_CELLS / rows) * (rules.WHITE_RC_DRAWS / 200)
    hours = rules.dual_c2_projection_hours(lattice_s, prepare_seconds, rc_s, chain_s,
                                           lattice_measured_fp64_on_device=measured_fp64_on_device)
    return {"hours": hours, "lattice_seconds": lattice_s, "quantile_seconds": prepare_seconds,
            "white_rc_seconds": rc_s, "chain_seconds": chain_s,
            "lattice_measured_fp64_on_device": measured_fp64_on_device,
            "method": "measured samples scaled linearly (lattice chunk, one segment's chains, RC slice)"}


def _write(root: Path, data_root: Path, name: str, payload: str | bytes, files: dict[str, str]) -> None:
    guarded_write(root / name, payload, data_root=data_root, output_root=root)
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    files[name] = hashlib.sha256(raw).hexdigest()


NOT_TESTED_NOTES = """# EXT2 W1 - not tested / disclosed limits

- R24 (funding) NOT TESTED; funding set aside; fee_bp = 0.
- Trade prints stand in for the mark price (no historical mark); liquidation fills at the worse of
  the derived level and the print.
- C2 study-1 disclosure: see receipt `c2_study1_disclosure`.
- Network detection: static import audit + runtime audit hook (C-extension sockets can bypass hooks).
- Every result is LIMITED SCOPE / NOT A VERDICT / SCREEN-LEVEL / EXPLORATORY / INVALID-for-fills.
"""


def run_ext2(*, data_root: Path, output_root: Path, manifest: Mapping[str, str] | None = None,
             mode: str = "real", calendar: Calendar | None = None,
             trades: Mapping[str, np.ndarray] | None = None, allow_cpu_fallback: bool = False,
             disk_free: int | None = None, pin_path: Path | None = None,
             forbidden_substrings: tuple[str, ...] = (), backend: Any = None,
             run_nonce: str | None = None, card_sha: str = CARD_SHA_V3, pr_head: str | None = None,
             supervised: bool = False, h1_seal_path: Path | None = None,
             cuda_check: Callable[[], bool] | None = None,
             prestart: Callable[[Any], dict[str, Any]] | None = None,
             projection_cap_hours: float = 2.0, announce: UnitAnnouncer | None = None) -> dict[str, Any]:
    """One EXT2 W1 run (A19 order). Returns the sealed worker receipt."""
    data_root = Path(data_root)
    root = Path(output_root)
    started = time.time()
    calendar = FROZEN if calendar is None else calendar
    card_sha12 = str(card_sha)[:12]
    stages: list[dict[str, Any]] = [{"stage": "import_pins", "device": "cpu", "fallback": False}]
    receipt: dict[str, Any] = {key: None for key in RECEIPT_REQUIRED_KEYS}
    receipt.update({
        "schema": "ext2_run_receipt_v2", "live": False, "paper_only": True, "edge_claim": False,
        "mode": mode, "status": "STOP", "stop_reason": "STOP", "reason": "STOP", "combos_searched": None,
        "devices": stages, "fallback": False, "card_sha": card_sha, "card_sha12": card_sha12,
        "pr_head": pr_head, "start_aedt": aedt_now(), "label": rules.LABEL if mode == "real" else SMOKE_LABEL,
        "fee_bp": FEE_BP,
    })
    files: dict[str, str] = {}
    announce = announce or UnitAnnouncer(root if supervised else None)
    root_ok = False
    try:
        if mode not in {"real", "smoke"}:
            raise Ext2Stop("STOP_MODE")
        if allow_cpu_fallback:
            raise Ext2Stop("STOP_CPU_FALLBACK_FORBIDDEN")
        if FEE_BP != 0.0:
            raise Ext2Stop("STOP_FEE")
        assert_roots(data_root, root)
        if supervised:
            if not root.is_dir():
                raise Ext2Stop("STOP_RUN_ROOT_MISSING")
        else:
            root.parent.mkdir(parents=True, exist_ok=True)
            try:
                root.mkdir(exist_ok=False)
            except FileExistsError:
                raise Ext2Stop("STOP_RUN_ROOT_EXISTS") from None
        root_ok = True
        if (root / COMPLETION_NAME).exists():
            raise Ext2Stop("STOP_STALE_COMPLETE")
        if (root / WORKER_RECEIPT_NAME).exists():
            raise Ext2Stop("STOP_RUN_ROOT_REUSED")
        nonce = run_nonce or os.environ.get(NONCE_ENV)
        if not nonce:
            raise Ext2Stop("STOP_NO_RUN_NONCE")
        receipt["run_nonce"] = nonce
        env_card = os.environ.get(CARD_ENV)
        if supervised and env_card and env_card != card_sha12:
            raise Ext2Stop("STOP_CARD_MISMATCH")
        pins = Path(pin_path) if pin_path is not None else PIN_PATH
        verify_import_pins(pins, SERVICE_ROOT)
        receipt["import_pins"] = {"path": "ci/ext2_import_pins.json", "sha256": _sha_file(pins)}
        violations = audit_files([Path(__file__).resolve().parent / name for name in SOURCE_FILES])
        if violations:
            raise Ext2Stop("STOP_NETWORK")
        stages.append({"stage": "source_audit", "device": "cpu", "fallback": False})
        if mode == "real" and not is_frozen(calendar):
            raise Ext2Stop("STOP_CALENDAR")
        receipt["windows"] = calendar.describe()["windows"]
        receipt["folds"] = calendar.describe()["folds"]
        receipt["calendar_label"] = calendar.label
        counted = count_combos()
        receipt["combos_searched"] = counted
        stages.append({"stage": "count", "device": "cpu", "fallback": False})
        if disk_free is not None and int(disk_free) < 100 * 1024 ** 3:
            raise Ext2Stop("STOP_DISK_FLOOR")
        # Device first: a real run never touches data without a verified GPU.
        if mode == "real":
            check = cuda_check or cuda_available
            if not check():
                stages.append({"stage": "exit_lattice", "device": None, "fallback": False, "status": "STOP"})
                raise Ext2Stop("STOP_GPU_MISSING")
            backend = backend or make_backend("torch", "cuda")
            announce("cuda_prestart", "gpu")
            pre = (prestart or cuda_prestart)(backend)
            receipt["cuda_prestart"] = pre
            if not pre.get("ok"):
                raise Ext2Stop("STOP_CUDA_PRESTART")
            if h1_seal_path is None or _sha_file(Path(h1_seal_path)) != H1_SEAL_SHA:
                raise Ext2Stop("STOP_H1_SEAL")
            receipt["h1_seal_sha"] = H1_SEAL_SHA
        else:
            backend = backend or NumpyBackend()
            parity = lattice_parity(backend)
            receipt["cuda_prestart"] = {"ok": False, "smoke": True, "not_cuda": True, "parity": parity}
            if not parity["pass"]:
                raise Ext2Stop("STOP_PARITY")
        device_kind = "gpu" if str(getattr(backend, "device", "cpu")).startswith("cuda") else "cpu"
        stages.append({"stage": "exit_lattice", "device": device_kind, "kernel": backend.name,
                       "fallback": False, "parity_test": False})
        blocked = real_run_device_stop(stages) if mode == "real" else None
        if blocked:
            raise Ext2Stop(blocked)
        # Load (A1-A4).
        announce("load", "cpu")
        if mode == "real":
            if manifest is None:
                raise Ext2Stop("STOP_MANIFEST")
            loaded = load_bitfinex(data_root, manifest)
            grid, trades_arr = loaded["grid"], loaded["trades"]
            receipt["allowlist_sha"] = aggregate_manifest_sha(manifest)
            if receipt["allowlist_sha"] != ALLOWLIST_AGGREGATE_SHA:
                raise Ext2Stop("STOP_ALLOWLIST")
            receipt["candle_cross_check"] = loaded["candle_cross_check"]
        else:
            if trades is None:
                raise Ext2Stop("STOP_SMOKE_NEEDS_TRADES")
            trades_arr = {k: np.asarray(v) for k, v in trades.items()}
            grid = build_grid(trades_arr, calendar.days())
            receipt["allowlist_sha"] = "SMOKE-synthetic"
        # Features, percentiles, regimes, thesis (A5-A8).
        announce("features", "cpu")
        t0 = time.monotonic()
        ctx = pipeline.prepare(grid, trades_arr, calendar)
        prepare_s = time.monotonic() - t0
        # Dry-check: coverage and projection (no P&L).
        announce("dry_check", "cpu")
        dry = pipeline.dry_check(ctx, calendar)
        receipt["undefined_stat_bars"] = dry["undefined_stat_bars"]
        receipt["dry_check"] = {k: dry[k] for k in ("structurally_untestable_cells", "empty_bar_count",
                                                    "trade_minute_share", "regime_undefined_scored_bars",
                                                    "first_sigma_utc", "scored_bars")}
        _write(root, data_root, "dry_check.json", pipeline.dumps(dry), files)
        announce("projection", device_kind)
        proj = _projection(ctx, lambda: pipeline.build_segments(ctx, calendar), backend, prepare_s,
                           measured_fp64_on_device=device_kind == "gpu")
        receipt["dual_c2_projection_hours"] = proj["hours"]
        receipt["projection"] = proj
        if proj["hours"] > float(projection_cap_hours):
            raise Ext2Stop("STOP_PROJECTION")
        # Search (A9-A17).
        announce("exit_lattice", device_kind)
        chunk_seq = {"n": 0}

        def on_chunk(index: int) -> None:
            chunk_seq["n"] += 1
            announce(f"lattice_chunk_{index}", device_kind)
        out = pipeline.run_search(ctx, backend, on_chunk=on_chunk)
        announce("outputs", "cpu")
        acc = out["acc"]
        receipt.update({k: acc.counts[k] for k in ("censored_close_count", "liquidation_count", "path_end_count")})
        receipt["dropped_count"] = acc.counts["dropped_count"]
        receipt["trades_oof"] = acc.counts["trades_oof"]
        receipt["stale_entry_bars"] = out["stale_entry_bars"]
        receipt["latency"] = out["latency"]
        base = out["base"]
        receipt["lattice"] = {"dtype": base["dtype"], "kernel": base["kernel"], "device": base["device"],
                              "chunks": base["chunks"], "max_chunk_seconds": max(base["chunk_seconds"] or [0.0])}
        gates = out["gates"]
        table = pipeline.cells_table(gates, calendar)
        receipt["gate_pass_counts"] = gates["gate_pass_counts"]
        receipt["leads"] = len(gates["lead_ids"])
        # Fixtures (C3b/C3c) and canary: FAIL = STOP.
        tau = int(calendar.folds[0].test_censor_ms()) - 3 * 3_600_000
        c3b = pipeline.fixture_c3b(lambda t: build_grid(t, calendar.days()), trades_arr, ctx, tau, calendar)
        c3c = pipeline.fixture_c3c(out["segments"])
        can = pipeline.canary(gates, backend=backend)
        plus = pipeline.plus_one_bar(ctx, out["segments"], base, gates)
        receipt["fixtures"] = {"C3b": c3b["pass"], "C3c": c3c["pass"], "canary": can["pass"]}
        receipt["canary_leads"] = can["canary_leads"]
        receipt["canary_bh_only_rejections"] = can["bh_only_rejections"]
        buf = io.BytesIO()
        np.savez_compressed(buf, cells=table)
        _write(root, data_root, "cells.npz", buf.getvalue(), files)
        _write(root, data_root, "leads.json", pipeline.dumps(pipeline.leads_json(gates, table)), files)
        _write(root, data_root, "fixtures.json", pipeline.dumps({"C3b": c3b, "C3c": c3c, "plus_one_bar": plus}),
               files)
        _write(root, data_root, "canary.json", pipeline.dumps(can), files)
        _write(root, data_root, "notes_not_tested.md", NOT_TESTED_NOTES, files)
        if not (c3b["pass"] and c3c["pass"]):
            raise Ext2Stop("STOP_FIXTURE_C3")
        if not can["pass"]:
            raise Ext2Stop("STOP_CANARY")
        receipt["status"] = "COMPLETED"
        receipt["stop_reason"] = None
        receipt["reason"] = "COMPLETED"
    except Ext2Stop as exc:
        receipt["status"] = "STOP"
        receipt["stop_reason"] = exc.reason
        receipt["reason"] = exc.reason
    receipt["devices"] = stages
    receipt["units"] = announce.log
    receipt["outputs"] = files
    receipt.update(rules.rule_receipt_fields())
    receipt["label"] = rules.LABEL if mode == "real" else SMOKE_LABEL
    receipt["end_aedt"] = aedt_now()
    receipt["wall_sec"] = round(time.time() - started, 3)
    sealed = seal_receipt(pipeline.jsonable(receipt), forbidden_substrings)
    if root_ok:
        try:
            guarded_write(root / WORKER_RECEIPT_NAME, json.dumps(sealed, sort_keys=True, indent=1),
                          data_root=data_root, output_root=root)
            if sealed["status"] == "COMPLETED":
                marker = {"run_nonce": sealed["run_nonce"], "card_sha12": card_sha12, "finished_aedt": aedt_now()}
                guarded_write(root / COMPLETION_NAME, json.dumps(marker), data_root=data_root, output_root=root)
        except Ext2Stop as exc:
            sealed["status"] = "STOP"
            sealed["stop_reason"] = exc.reason
            sealed["reason"] = exc.reason
    return sealed


def main(argv: list[str] | None = None) -> int:
    """Worker entry under the supervisor: cwd = run root. Exit 0 only on COMPLETED."""
    import argparse
    parser = argparse.ArgumentParser(prog="ext2_runner")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--manifest", required=True, help="JSON {relpath: sha256}")
    parser.add_argument("--h1-seal", required=True)
    parser.add_argument("--pr-head", default=None)
    args = parser.parse_args(argv)
    install_network_audit_hook()
    root = Path.cwd()
    beat = HeartbeatThread(root / HEARTBEAT_NAME, interval_sec=5.0)
    beat.start()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        receipt = run_ext2(data_root=Path(args.data_root), output_root=root, manifest=manifest, mode="real",
                           supervised=True, h1_seal_path=Path(args.h1_seal), pr_head=args.pr_head)
    finally:
        beat.stop()
    return 0 if receipt.get("status") == "COMPLETED" else 3


if __name__ == "__main__":
    sys.exit(main())

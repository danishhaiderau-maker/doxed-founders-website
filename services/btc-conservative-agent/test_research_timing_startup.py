import hashlib
import json
import os

import pytest

import research_timing_startup as startup
from research_timing_capture import load_runtime_timing_config, materialize_timing_declarations


def fixture(tmp_path):
    config = {"schema": "research_timing_config_v1", "epoch_id": "epoch-old",
              "source_revision": "a" * 40, "tile_config_signature": "b" * 64,
              "activated_at_ts": 100, "delay_seconds": [0, 1, 3, 5],
              "ordering_treatment": "FIXED_EXPIRY_CANCEL_BEFORE_REPLACE",
              "evidence_basis": "DECLARED_SIMULATION"}
    raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "old.json"
    path.write_bytes(raw)
    output = tmp_path / "versions"
    output.mkdir()
    pins = {"BTC_RESEARCH_TIMING_CONFIG_FILE": str(path),
            "BTC_RESEARCH_TIMING_CONFIG_SHA256": hashlib.sha256(raw).hexdigest()}
    args = dict(environ=pins, source_revision="c" * 40, epoch_id="epoch-current",
                tile_config_signature="d" * 64, output_directory=output, startup_time=200)
    return config, path, args


def test_forward_only_preserves_explicit_model_and_environment(tmp_path):
    old, path, args = fixture(tmp_path)
    before = path.read_bytes()
    environment = dict(os.environ)
    original_pins = dict(args["environ"])
    pins = startup.prepare_startup_timing_declaration(**args)
    loaded = load_runtime_timing_config(pins)
    config = loaded["research_timing_config"]
    for key in ("delay_seconds", "ordering_treatment", "evidence_basis", "schema"):
        assert config[key] == old[key]
    assert config["activated_at_ts"] == 200
    assert config["source_revision"] == args["source_revision"]
    assert config["epoch_id"] == args["epoch_id"]
    assert config["tile_config_signature"] == args["tile_config_signature"]
    row = {**config, **loaded, "signal_ts": 201}
    captures = {"directional_schedules": {side: {"direction": side, "capture_signature": side}
                                         for side in ("LONG", "SHORT")}}
    result = materialize_timing_declarations(row, captures)
    assert result["status"] == "DECLARED" and len(result["declarations"]) == 8
    assert {r["source_capture_signature"] for r in result["declarations"]} == {"LONG", "SHORT"}
    assert materialize_timing_declarations({**row, "signal_ts": 199}, captures)["reason"] == "TIMING_CONFIG_NOT_PRE_SIGNAL"
    assert path.read_bytes() == before and args["environ"] == original_pins
    assert dict(os.environ) == environment
    with pytest.raises(ValueError, match="PUBLICATION_FAILED"):
        startup.prepare_startup_timing_declaration(**args)
    assert load_runtime_timing_config(pins) == loaded


@pytest.mark.parametrize("defect", ["missing", "hash", "delay", "identity", "time", "relative", "missing_directory"])
def test_invalid_inputs_never_publish(tmp_path, defect):
    old, path, args = fixture(tmp_path)
    if defect == "missing": args["environ"] = {}
    if defect == "hash": args["environ"]["BTC_RESEARCH_TIMING_CONFIG_SHA256"] = "0" * 64
    if defect == "delay":
        old["delay_seconds"] = [True]
        raw = json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(raw)
        args["environ"]["BTC_RESEARCH_TIMING_CONFIG_SHA256"] = hashlib.sha256(raw).hexdigest()
    if defect == "identity": args["source_revision"] = "short"
    if defect == "time": args["startup_time"] = 99
    if defect == "relative": args["output_directory"] = "relative"
    if defect == "missing_directory": args["output_directory"] = tmp_path / "missing"
    with pytest.raises((ValueError, OSError)):
        startup.prepare_startup_timing_declaration(**args)
    assert not list((tmp_path / "versions").iterdir())


def test_publication_failure_leaves_no_pin_or_partial_final(tmp_path, monkeypatch):
    _, _, args = fixture(tmp_path)
    def fail(*args): raise OSError("disk failure")
    monkeypatch.setattr(startup.os, "link", fail)
    with pytest.raises(ValueError, match="PUBLICATION_FAILED"):
        startup.prepare_startup_timing_declaration(**args)
    assert not list(args["output_directory"].iterdir())


def test_linked_directory_rejected(tmp_path):
    _, _, args = fixture(tmp_path)
    link = tmp_path / "link"
    try:
        link.symlink_to(args["output_directory"], target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    args["output_directory"] = link
    with pytest.raises(ValueError):
        startup.prepare_startup_timing_declaration(**args)

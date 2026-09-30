"""Forward-trial freeze: refuses without a qualifying tile, write-once manifest, no runtime effect."""
import copy
import json
import os
import stat

import pytest

from research import forward_trial_freeze as ft

EPOCH = "epoch-test"
T0 = 1_790_000_000.0
DAY = 86400.0
LANES = ("TILE_X", "TILE_Y")
REG_SIG = "registry-sig"


def _registry():
    base = {"paper_only": True, "platform_relay_eligible": False, "requested_margin_usd": 0.25,
            "entry_policy": {"offset_pct": 0.3, "chase_windows": (2, 3, 4)}, "risk_limits": {"account_risk_pct": 0.5}}
    return {"TILE_X": {**base, "label": "X", "raw_policy_id": "X|1", "policy_signature": "sig-x",
                       "exit_policy": {"family": "CHANDELIER"}, "ladder": ((8, 5),)},
            "TILE_Y": {**base, "label": "Y", "raw_policy_id": "Y|1", "policy_signature": "sig-y",
                       "exit_policy": {"family": "ATR_TRAIL"}}}


def _evidence(x_ci=(-0.01, 0.02), x_n=40):
    return {"epoch_id": EPOCH, "ev_ranking": {"rows": [
        {"lane": "TILE_X", "n": x_n, "mean_usd": 0.02, "ci95_usd": list(x_ci), "status": "RANKED" if x_n >= 30 else "NOT_ENOUGH_DATA"},
        {"lane": "TILE_Y", "n": 35, "mean_usd": -0.001, "ci95_usd": [-0.01, 0.008], "status": "RANKED"},
    ]}}


def _selector(x_consistent=True):
    tile = {"oos_consistent_positive": True, "identity_single_signature": True,
            "chronological_halves": {"status": "CONSISTENT_POSITIVE"}}
    return {"epoch_id": EPOCH, "tiles": {
        "TILE_X": {**tile, "oos_consistent_positive": x_consistent, "observed_policy_signatures": {"sig-x": 40}},
        "TILE_Y": {**tile, "observed_policy_signatures": {"sig-y": 35}}}}


def _report(tmp_path, *, evidence=None, selector=None, registry=None, trades=(), now=T0, runtime=REG_SIG,
            key=None, auto=True):
    return ft.build_forward_trial_report(
        evidence=evidence if evidence is not None else _evidence(x_ci=(0.005, 0.03)),
        selector=selector if selector is not None else _selector(),
        registry=registry or _registry(), tile_order=LANES, trades=list(trades), directory=tmp_path / "trial",
        registry_signature=REG_SIG, runtime_registry_signature=runtime, analyzer_revision="rev",
        now=now, auto_freeze=auto, signing_key=key)


def _trade(lane, pnl, decision, sig=None):
    return {"trade_id": f"{lane}-{decision}", "research_lane": lane, "epoch_id": EPOCH, "net_pnl_usd": pnl,
            "shared_ai_call_ts": decision, "close_ts": decision + 600, "exit_reason": "TRAIL_STOP",
            "policy_signature": sig or {"TILE_X": "sig-x", "TILE_Y": "sig-y"}[lane]}


def test_refuses_to_freeze_when_no_tile_qualifies(tmp_path):
    report = _report(tmp_path, evidence=_evidence())
    assert report["status"] == "NO_QUALIFYING_CANDIDATE"
    assert "ev_ci95_above_zero" in report["status_text"]
    assert not (tmp_path / "trial").exists()
    assert "manifest" not in report


@pytest.mark.parametrize("override, gate", [
    ({"evidence": _evidence(x_ci=(0.005, 0.03), x_n=29)}, "n_at_least_30"),
    ({"selector": _selector(x_consistent=False)}, "oos_consistent_positive"),
    ({"runtime": None}, "registry_runtime_parity"),
    ({"runtime": "other-sig"}, "registry_runtime_parity"),
    ({"selector": {**_selector(), "epoch_id": "old"}}, "reports_current_same_epoch"),
])
def test_each_gate_blocks_the_freeze(tmp_path, override, gate):
    report = _report(tmp_path, **override)
    assert report["status"] == "NO_QUALIFYING_CANDIDATE"
    x = next(c for c in report["candidates"] if c["lane"] == "TILE_X")
    assert gate in x["failed_gates"]
    assert not (tmp_path / "trial").exists()


def test_freeze_writes_one_read_only_manifest_without_touching_the_registry(tmp_path):
    registry = _registry()
    before = copy.deepcopy(registry)
    report = _report(tmp_path, registry=registry)
    assert report["status"] == "TRIAL_ACTIVE"
    manifest = report["manifest"]
    assert manifest["candidate"]["lane"] == "TILE_X" and manifest["control"]["lane"] == "TILE_Y"
    assert manifest["candidate"]["policy_signature"] == "sig-x"
    assert manifest["registry_signature"] == REG_SIG
    assert manifest["runtime_effect"] == "NONE" and manifest["relay_eligibility_change"] == "NONE"
    assert manifest["candidate"]["relay_eligible"] is False and manifest["control"]["paper_only"] is True
    assert ft.verify_manifest(manifest) == []
    assert registry == before
    files = list((tmp_path / "trial").glob("freeze-*.json"))
    assert len(files) == 1
    assert not os.stat(files[0]).st_mode & stat.S_IWRITE
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    assert ft.verify_manifest(on_disk) == [] and on_disk["manifest_id"] == manifest["manifest_id"]
    again = _report(tmp_path, now=T0 + 3600)
    assert again["manifest"]["manifest_id"] == manifest["manifest_id"]
    assert len(list((tmp_path / "trial").glob("freeze-*.json"))) == 1
    with pytest.raises(ValueError, match="ALREADY_ACTIVE"):
        ft.write_manifest(tmp_path / "trial", {**manifest, "manifest_id": "other"})


def test_eligible_without_auto_freeze_writes_nothing(tmp_path):
    report = _report(tmp_path, auto=False)
    assert report["status"] == "ELIGIBLE_TO_FREEZE"
    assert not (tmp_path / "trial").exists()


def test_tracker_reports_daily_ev_and_completes_after_15_days(tmp_path):
    frozen = _report(tmp_path)["manifest"]
    assert frozen["trial_days"] == 15
    trades = [_trade("TILE_X", 0.02 + 0.001 * (i % 3), T0 + 600 + i * 7200) for i in range(40)]
    trades += [_trade("TILE_Y", -0.001, T0 + 600 + i * 7200) for i in range(40)]
    trades.append(_trade("TILE_X", 5.0, T0 - 3600))  # before the freeze: never counted
    mid = _report(tmp_path, trades=trades, now=T0 + 2.5 * DAY)
    assert mid["status"] == "TRIAL_ACTIVE" and mid["tracker"]["day"] == 3
    assert mid["tracker"]["daily"][0]["candidate"]["n"] == 12
    assert mid["tracker"]["daily"][0]["ev_diff_usd"] > 0
    done = _report(tmp_path, trades=trades, now=T0 + 16 * DAY)
    assert done["tracker"]["day"] == 15
    assert done["status"] == "COMPLETE_CANDIDATE_HELD"
    assert done["tracker"]["cumulative"]["candidate"]["n"] == 40


def test_identity_drift_invalidates_the_trial(tmp_path):
    _report(tmp_path)
    drifted = [_trade("TILE_X", 0.01, T0 + 600, sig="sig-new")]
    report = _report(tmp_path, trades=drifted, now=T0 + DAY)
    assert report["status"] == "INVALIDATED_IDENTITY_DRIFT"
    assert "CANDIDATE_UNFROZEN_SIGNATURE_IN_TRADES" in report["tracker"]["drift"]
    registry = _registry()
    registry["TILE_Y"]["platform_relay_eligible"] = True
    report = _report(tmp_path, registry=registry, now=T0 + DAY)
    assert "CONTROL_NO_LONGER_PAPER_ONLY_RELAY_INELIGIBLE" in report["tracker"]["drift"]


def test_tampered_manifest_and_hmac_are_detected(tmp_path):
    manifest = _report(tmp_path, key=b"k1")["manifest"]
    assert manifest["signature"]["kind"] == "HMAC_SHA256"
    assert ft.verify_manifest(manifest, b"k1") == []
    assert "HMAC_MISMATCH" in ft.verify_manifest(manifest, b"k2")
    assert "HMAC_KEY_UNAVAILABLE" in ft.verify_manifest(manifest)
    tampered = {**manifest, "candidate": {**manifest["candidate"], "relay_eligible": True}}
    problems = ft.verify_manifest(tampered, b"k1")
    assert "CONTENT_SHA256_MISMATCH" in problems and "CANDIDATE_NOT_PAPER_ONLY_RELAY_INELIGIBLE" in problems


def test_runtime_registry_signature_requires_a_fresh_ok_snapshot(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"ok": True, "observedAt": "2026-09-30T13:15:02.0195775+00:00",
                                "tile_registry_signature": "abc"}), encoding="utf-8")
    observed = 1790774102.0
    assert ft.runtime_registry_signature(path, now=observed + 60) == "abc"
    assert ft.runtime_registry_signature(path, now=observed + 7200) is None
    assert ft.runtime_registry_signature(tmp_path / "missing.json") is None


def test_trial_dir_refuses_onedrive(monkeypatch):
    monkeypatch.setenv("FORWARD_TRIAL_DIR", r"C:\Users\x\OneDrive\trial")
    with pytest.raises(ValueError):
        ft.trial_dir()

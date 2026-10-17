"""full-reset-no-archive: explicit KEEP allowlist, fail-closed pre-checks, receipt-first, idempotent, never at boot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pytest

import clean_epoch_wipe as wipe
import full_reset_no_archive as frna
import research_freeze

REV = "5164548a1234"
FLAGS = {"live_armed": False, "bitfinex_live_enabled": False, "force_paper_mode": True, "live_eligible_switch": 0,
         "tile_enabled": {"FAMILY_DISLOC_BFX_DISLOCATION_1M": False, "FAMILY_DISLOC_FOI_LONG_1M": False}}
HEALTH = {"execution_paused": True, "pause_owner": "DEPLOY_MAINTENANCE", "live_armed": False,
          "bitfinex_live_enabled": False, "force_paper_mode": True, "source_git_rev": REV}
ENV = {"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1"}
OVERRIDE = {"confirmation": "BREAK_21_DAY_RESEARCH_FREEZE", "reason": "Danish 17 Oct 18:09 full reset, no archive"}


def _w(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def vol(tmp_path):
    rt = tmp_path / "runtime"
    # allowlisted state (live-off flags, arming, settings, spread gate, tile switches, boot config)
    _w(rt / "config-7002.json", json.dumps(FLAGS, sort_keys=True))
    _w(rt / "state.json", json.dumps({"live_armed": False, "tiles": {"6": "OFF", "7": "OFF"}}))
    _w(rt / "policy.json", "{}")
    _w(rt / "spread-gate.json", json.dumps({"max_bp": 3}))
    _w(rt / "paper_lifecycle_v1.json", json.dumps({"paper_only": True, "live_armed": False, "positions": [],
                                                    "pending_orders": []}))
    _w(rt / "open_positions.json", "[]")
    _w(rt / "fresh_start_epoch.json", json.dumps({"epoch_ts": 1.0}))
    _w(rt / "execution_settings_history.jsonl", '{"a":1}\n')
    # data to delete (rows, shadow, tape, book, deriv, DISLOC, rotations, forced rows)
    _w(rt / "trades_3factor.csv", "trade_id,exit_status\nT1,PAPER_FILL_FORCED\nT2,PAPER_FILL\n")
    _w(rt / "trades_3factor.csv.1", "trade_id\nT0\n")
    _w(rt / "expired_orders_3factor.csv")
    _w(rt / "paper_ioc_no_fill_shadow.jsonl")
    _w(rt / "paper_ioc_no_fill_shadow.jsonl.1")
    _w(rt / "market_microstructure_1s.jsonl")
    _w(rt / "market_microstructure_1s.jsonl.3")
    _w(rt / "bfx_book_10l_1s.jsonl")
    _w(rt / "bfx_book_10l_1s.jsonl.1")
    _w(rt / "bfx_deriv_status_1s.jsonl")
    _w(rt / "bfx_trades.jsonl")
    _w(rt / "venue_bbo_1m.jsonl")
    _w(rt / "cross_venue_tape_1m.jsonl.2")
    _w(rt / "disloc_trades.jsonl")
    _w(rt / "disloc_foi_trades.jsonl.4.gz")
    _w(rt / "shadow_exit_paths.jsonl")
    _w(rt / "xvp_shadow_signals.jsonl")
    _w(rt / "ai_shadow_challengers.jsonl")
    _w(rt / "lane_pnl_ledger.json")
    _w(rt / "some_new_stream.jsonl")
    # retained by design
    _w(rt / "v3" / "ledgers" / "order_intent.jsonl")
    _w(rt / "research.db")
    _w(rt / "research_events_v22.jsonl")
    _w(rt / "research_events_v22.jsonl.2")
    _w(rt / "relay_outbox_quarantine.jsonl")
    _w(rt / "bot.py")
    _w(rt / "vault" / "x.env")
    _w(rt / "forward-tracker" / "chain.jsonl")
    _w(rt / "mystery_blob.bin")  # unclassified: retained and reported
    _w(rt / "sub" / "unknown.jsonl")  # unclassified (not top level)
    return tmp_path


def _plan(vol):
    return frna.build_plan(vol)


def test_classification_table_is_pinned():
    k, d, u = frna.KEEP, frna.DELETE, frna.UNCLASSIFIED
    table = {
        "config-7002.json": k, "state.json": k, "spread-gate.json": k, "fresh_start_epoch.json": k,
        "execution_settings_history.jsonl": k, "paper_lifecycle_v1.json": k, "v3/ledgers/x.jsonl": k,
        "research.db": k, "research_events_v22.jsonl.7": k, "relay_outbox_quarantine.jsonl": k,
        "full-reset-receipts/abc/receipt-intent.json": k, "bot.py": k, "vault/home.env": k,
        "forward-tracker/chain.jsonl": k, "archive_receipts.jsonl": k, "live_drills.jsonl.1": k, "fill_markouts.jsonl": d,
        "dyn_regime_minutes.jsonl": d, "trades_3factor.csv": d, "trades_3factor.csv.1": d,
        "paper_ioc_no_fill_shadow.jsonl.2": d, "market_microstructure_1s.jsonl.14": d,
        "bfx_book_10l_1s.jsonl": d, "bfx_deriv_status_1s.jsonl.1": d, "bfx_trades.jsonl": d,
        "venue_bbo_1m.jsonl": d, "disloc_trades.jsonl": d, "disloc_foi_trades.jsonl.4.gz": d,
        "lane_pnl_ledger.json": d, "xvp_shadow_signals.jsonl": d, "tierA/bitfinex_l1_tape_1s/v1/p.parquet": d,
        "never_heard_of_it.jsonl": d, "never_heard_of_it.jsonl.1": d,
        "mystery_blob.bin": u, "sub/unknown.jsonl": u,
    }
    for rel, want in table.items():
        assert frna.classify(rel)[0] == want, (rel, frna.classify(rel))


def test_allowlist_wins_over_every_delete_class():
    # a name that is both allowlisted and a stream suffix is KEPT (explicit allowlist first)
    assert frna.classify("execution_settings_history.jsonl")[0] == frna.KEEP
    for name in frna.KEEP_EXACT_NAMES:
        assert frna.classify(name)[0] == frna.KEEP, name
    assert not (frna.KEEP_EXACT_NAMES & frna.DELETE_EXACT_NAMES)


def test_plan_is_read_only_lists_unclassified_and_pins_digests(vol):
    before = sorted(str(p) for p in vol.rglob("*"))
    plan = _plan(vol)
    assert sorted(str(p) for p in vol.rglob("*")) == before  # nothing changed
    rels = {c["rel"] for c in plan["candidates"]}
    assert {"trades_3factor.csv", "trades_3factor.csv.1", "bfx_book_10l_1s.jsonl", "bfx_book_10l_1s.jsonl.1",
            "paper_ioc_no_fill_shadow.jsonl", "disloc_foi_trades.jsonl.4.gz", "market_microstructure_1s.jsonl.3",
            "some_new_stream.jsonl", "lane_pnl_ledger.json"} <= rels
    for kept in ("config-7002.json", "state.json", "v3/ledgers/order_intent.jsonl", "research.db",
                 "research_events_v22.jsonl", "relay_outbox_quarantine.jsonl", "bot.py", "vault/x.env"):
        assert kept not in rels
    assert {u["rel"] for u in plan["unclassified_retained"]} == {"mystery_blob.bin", "sub/unknown.jsonl"}
    assert "config-7002.json" in plan["keep_digests"] and plan["keep_digests"]["config-7002.json"] == _sha(vol / "runtime/config-7002.json")
    assert frna.confirm_token(plan["plan_sha256"]) == "DELETE-FULL-RESET:" + plan["plan_sha256"][:12]


def test_execute_deletes_all_data_and_live_flags_are_byte_identical(vol):
    rt = vol / "runtime"
    keep_files = ["config-7002.json", "state.json", "policy.json", "spread-gate.json", "paper_lifecycle_v1.json",
                  "open_positions.json", "fresh_start_epoch.json", "execution_settings_history.jsonl"]
    before = {name: (rt / name).read_bytes() for name in keep_files}
    plan = _plan(vol)
    receipt = frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                           freeze_override=OVERRIDE)
    assert receipt["completed"] is True and receipt["keep_flags_changed"] == []
    for name in keep_files:
        assert (rt / name).read_bytes() == before[name], name  # BYTE identical
    live = json.loads((rt / "config-7002.json").read_text())
    assert live["live_armed"] is False and live["bitfinex_live_enabled"] is False
    assert live["force_paper_mode"] is True and live["live_eligible_switch"] == 0
    assert live["tile_enabled"] == FLAGS["tile_enabled"]  # tiles 6/7 stay OFF
    for rel in ("trades_3factor.csv", "trades_3factor.csv.1", "bfx_book_10l_1s.jsonl", "bfx_book_10l_1s.jsonl.1",
                "paper_ioc_no_fill_shadow.jsonl", "paper_ioc_no_fill_shadow.jsonl.1", "disloc_trades.jsonl",
                "disloc_foi_trades.jsonl.4.gz", "market_microstructure_1s.jsonl", "market_microstructure_1s.jsonl.3",
                "cross_venue_tape_1m.jsonl.2", "shadow_exit_paths.jsonl", "bfx_deriv_status_1s.jsonl",
                "xvp_shadow_signals.jsonl", "ai_shadow_challengers.jsonl", "lane_pnl_ledger.json"):
        assert not (rt / rel).exists(), rel
    for rel in ("v3/ledgers/order_intent.jsonl", "research.db", "research_events_v22.jsonl", "bot.py",
                "mystery_blob.bin", "sub/unknown.jsonl", "forward-tracker/chain.jsonl"):
        assert (rt / rel).exists(), rel
    assert frna.verify_after(plan)["ok"] is True
    # receipts survive the wipe
    rdir = rt / frna.RECEIPTS_DIRNAME / plan["plan_sha256"][:12]
    assert (rdir / frna.INTENT_NAME).is_file() and (rdir / frna.COMPLETE_NAME).is_file()
    assert frna.classify(f"{frna.RECEIPTS_DIRNAME}/x/{frna.INTENT_NAME}")[0] == frna.KEEP


def test_intent_receipt_is_written_and_synced_before_the_first_unlink(vol):
    rt = vol / "runtime"
    plan = _plan(vol)
    seen = []

    def spy_unlink(path):
        intent = rt / frna.RECEIPTS_DIRNAME / plan["plan_sha256"][:12] / frna.INTENT_NAME
        assert intent.is_file(), "intent receipt must exist before any deletion"
        doc = json.loads(intent.read_text())
        seen.append(doc["will_delete_files"])
        os.unlink(path)

    frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                 freeze_override=OVERRIDE, unlink=spy_unlink)
    assert seen and seen[0] == plan["total_delete_files"]
    doc = json.loads((rt / frna.RECEIPTS_DIRNAME / plan["plan_sha256"][:12] / frna.INTENT_NAME).read_text())
    assert doc["git_rev"] == REV and doc["freeze_override"]["confirmation"] == "BREAK_21_DAY_RESEARCH_FREEZE"
    assert doc["reason"] == "full reset no archive" and doc["delete_by_class"] and doc["archive"].startswith("NONE")


def test_a_changed_live_flag_is_detected_and_fails(vol):
    rt = vol / "runtime"
    plan = _plan(vol)

    def tamper(path):
        os.unlink(path)
        (rt / "config-7002.json").write_text(json.dumps({**FLAGS, "live_armed": True}))

    with pytest.raises(frna.FullResetRefused, match="LIVE_FLAGS_CHANGED"):
        frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                     freeze_override=OVERRIDE, unlink=tamper)
    done = json.loads((rt / frna.RECEIPTS_DIRNAME / plan["plan_sha256"][:12] / frna.COMPLETE_NAME).read_text())
    assert done["completed"] is False and done["keep_flags_changed"] == ["config-7002.json"]
    assert frna.completed_receipt(rt / frna.RECEIPTS_DIRNAME, plan["plan_sha256"]) is None


def test_a_tile_switch_flipped_on_is_a_changed_flag(vol):
    rt = vol / "runtime"
    plan = _plan(vol)
    flipped = json.loads(json.dumps(FLAGS))
    flipped["tile_enabled"]["FAMILY_DISLOC_FOI_LONG_1M"] = True

    def tamper(path):
        os.unlink(path)
        (rt / "config-7002.json").write_text(json.dumps(flipped))

    with pytest.raises(frna.FullResetRefused, match="LIVE_FLAGS_CHANGED"):
        frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                     freeze_override=OVERRIDE, unlink=tamper)


def test_a_paused_bot_rewriting_its_heartbeat_is_informational_not_a_failure(vol):
    rt = vol / "runtime"
    (rt / "state.json").write_text(json.dumps({"live_armed": False, "tiles": {"6": "OFF", "7": "OFF"}, "heartbeat": 1}))
    plan = _plan(vol)

    def bot_heartbeat(path):
        os.unlink(path)
        (rt / "state.json").write_text(json.dumps({"live_armed": False, "tiles": {"6": "OFF", "7": "OFF"}, "heartbeat": 2}))

    done = frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                        freeze_override=OVERRIDE, unlink=bot_heartbeat)
    assert done["completed"] is True and done["keep_flags_changed"] == []
    assert done["keep_bytes_changed_informational"] == ["state.json"]


def test_unlink_is_only_ever_called_on_delete_class_paths(vol):
    rt = vol / "runtime"
    plan = _plan(vol)
    calls = []

    def spy(path):
        calls.append(os.path.relpath(path, rt).replace(os.sep, "/"))
        os.unlink(path)

    frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                 freeze_override=OVERRIDE, unlink=spy)
    assert calls and all(frna.classify(rel)[0] == frna.DELETE for rel in calls), calls


def test_a_file_still_being_appended_is_deleted_and_plan_identity_ignores_sizes(vol):
    rt = vol / "runtime"
    plan = _plan(vol)
    with open(rt / "bfx_trades.jsonl", "a") as fh:  # a collector appends after planning
        fh.write("late row\n")
    assert _plan(vol)["plan_sha256"] == plan["plan_sha256"]
    done = frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                        freeze_override=OVERRIDE)
    assert done["completed"] is True and not (rt / "bfx_trades.jsonl").exists()
    (rt / "brand_new_rotation.jsonl.9").write_text("x")  # a NEW file changes the identity: operator must re-plan
    assert _plan(vol)["plan_sha256"] != plan["plan_sha256"]


def test_reason_is_required_before_any_receipt_or_deletion(vol):
    plan = _plan(vol)
    with pytest.raises(frna.FullResetRefused, match="REASON_REQUIRED"):
        frna.execute(plan, receipts_dir=vol / "runtime" / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="short",
                     freeze_override=OVERRIDE)
    assert (vol / "runtime" / "trades_3factor.csv").exists()
    assert not (vol / "runtime" / frna.RECEIPTS_DIRNAME).exists()


# ---------------------------------------------------------------- CLI gates: each one aborts BEFORE any deletion

def _args(vol, **kw):
    base = dict(command="execute", scope="fly", data_root=str(vol), receipts_dir=None, out=None, epoch=None,
                confirm="", expect_plan_sha256="", health_url=None, expect_git_rev=REV,
                reason="full reset no archive", freeze_override_confirmation=OVERRIDE["confirmation"],
                freeze_override_reason=OVERRIDE["reason"])
    base.update(kw)
    return argparse.Namespace(**base)


def _good(vol, **kw):
    plan = _plan(vol)
    base = {"confirm": frna.confirm_token(plan["plan_sha256"]), "expect_plan_sha256": plan["plan_sha256"]}
    return _args(vol, **{**base, **kw})


def _run(args, health=None, env=None):
    return frna.run_cli(args, fetch=lambda url: dict(HEALTH if health is None else health), env=ENV if env is None else env)


def _nothing_deleted(vol):
    return all((vol / "runtime" / rel).exists() for rel in ("trades_3factor.csv", "bfx_book_10l_1s.jsonl", "bfx_trades.jsonl"))


def test_cli_plan_is_dry_run_and_prints_the_confirm_token(vol, capsys):
    assert _run(_args(vol, command="plan")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["confirm_token"].startswith("DELETE-FULL-RESET:")
    assert out["unclassified_retained_count"] == 2 and _nothing_deleted(vol)


def test_cli_happy_path_then_rerun_is_a_noop(vol, capsys):
    args = _good(vol)
    assert _run(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["executed"] is True and out["verify"]["ok"] is True and out["completed"] is True
    assert not (vol / "runtime" / "trades_3factor.csv").exists()
    assert _run(args) == 0  # idempotent: same plan sha -> ALREADY_DONE, nothing to do
    again = json.loads(capsys.readouterr().out)
    assert again["already_done"] is True and again["executed"] is False


@pytest.mark.parametrize("mutate,code", [
    ({"confirm": "DELETE-FULL-RESET:000000000000"}, 5),
    ({"expect_plan_sha256": "0" * 64}, 6),
    ({"expect_git_rev": "deadbeef0000"}, 7),
    ({"expect_git_rev": ""}, 7),
])
def test_cli_gates_abort_without_deleting(vol, mutate, code):
    assert _run(_good(vol, **mutate)) == code
    assert _nothing_deleted(vol)


@pytest.mark.parametrize("health", [
    {**HEALTH, "live_armed": True},
    {**HEALTH, "bitfinex_live_enabled": True},
    {**HEALTH, "force_paper_mode": False},
    {**HEALTH, "execution_paused": False},
    {**HEALTH, "pause_owner": "ADMIN_MANUAL"},
    {**HEALTH, "exchange_positions": 1},
    {**HEALTH, "exchange_delta_btc": 0.0003},
    {**HEALTH, "source_git_rev": "ffffffffffff"},
])
def test_cli_precheck_failures_abort_without_deleting(vol, health):
    assert _run(_good(vol), health=health) == 7
    assert _nothing_deleted(vol)
    assert not (vol / "runtime" / frna.RECEIPTS_DIRNAME).exists()  # not even an intent receipt


def test_cli_health_unavailable_fails_closed(vol):
    def boom(url):
        raise OSError("down")
    assert frna.run_cli(_good(vol), fetch=boom, env=ENV) == 7 and _nothing_deleted(vol)


@pytest.mark.parametrize("lifecycle", [
    {"paper_only": True, "live_armed": False, "positions": [{"id": 1}], "pending_orders": []},
    {"paper_only": True, "live_armed": False, "positions": [], "pending_orders": [{"id": 1}]},
    {"paper_only": False, "live_armed": False, "positions": [], "pending_orders": []},
    {"paper_only": True, "live_armed": True, "positions": [], "pending_orders": []},
])
def test_open_paper_position_or_arming_in_lifecycle_aborts(vol, lifecycle):
    (vol / "runtime" / "paper_lifecycle_v1.json").write_text(json.dumps(lifecycle))
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


def test_open_positions_file_or_missing_lifecycle_aborts(vol):
    (vol / "runtime" / "open_positions.json").write_text('[{"trade_id": "P1"}]')
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)
    (vol / "runtime" / "open_positions.json").write_text("[]")
    (vol / "runtime" / "paper_lifecycle_v1.json").unlink()
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


def test_live_flag_in_config_aborts(vol):
    (vol / "runtime" / "config-7002.json").write_text(json.dumps({**FLAGS, "live_armed": True}))
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


def test_per_wipe_prefix_flag_is_required(vol):
    assert _run(_good(vol), env={}) == 7 and _nothing_deleted(vol)
    assert _run(_good(vol), env={"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "0"}) == 7 and _nothing_deleted(vol)


def test_freeze_override_is_required_inside_the_freeze_and_exact(vol, monkeypatch):
    monkeypatch.setattr(research_freeze, "freeze_status", lambda manifest, now, configured_epoch=None: {
        "guarded": True, "status": "ACTIVE", "ends_at_utc": "2026-10-25T08:13:00Z", "epoch_id": "x"})
    for confirmation, reason in (("", ""), ("BREAK_21_DAY_RESEARCH_FREEZE", "short"), ("yes", "long enough reason here"),
                                 ("break_21_day_research_freeze", "long enough reason here")):
        args = _good(vol, freeze_override_confirmation=confirmation, freeze_override_reason=reason)
        assert _run(args) == 8, (confirmation, reason)
        assert _nothing_deleted(vol)
    assert _run(_good(vol)) == 0  # the documented override with a written reason is accepted
    doc = json.loads(next((vol / "runtime" / frna.RECEIPTS_DIRNAME).glob("*/receipt-complete.json")).read_text())
    assert doc["freeze_override"]["reason"] == OVERRIDE["reason"]


def test_environment_override_does_not_bypass_the_cli_gate(vol, monkeypatch):
    monkeypatch.setattr(research_freeze, "freeze_status", lambda manifest, now, configured_epoch=None: {
        "guarded": True, "status": "ACTIVE", "ends_at_utc": None, "epoch_id": "x"})
    monkeypatch.setenv(research_freeze.OVERRIDE_ENV, research_freeze.OVERRIDE_CONFIRMATION)
    monkeypatch.setenv(research_freeze.OVERRIDE_REASON_ENV, "an ambient override that was never passed explicitly")
    assert _run(_good(vol, freeze_override_confirmation="", freeze_override_reason="")) == 8


def test_laptop_scope_is_refused(vol, capsys):
    assert frna.run_cli(_args(vol, scope="laptop", command="plan"), fetch=lambda u: HEALTH, env=ENV) == 2


# ---------------------------------------------------------------- wiring: flag in clean_epoch_wipe, never at boot

def test_clean_epoch_wipe_flag_delegates_and_rejects_mixing(vol, capsys, monkeypatch):
    assert wipe.main(["plan", "--scope", "fly", "--data-root", str(vol), "--full-reset-no-archive"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "full-reset-no-archive" and out["dry_run"] is True
    with pytest.raises(SystemExit):
        wipe.main(["plan", "--scope", "fly", "--data-root", str(vol), "--full-reset-no-archive", "--pre-start"])
    # existing modes are untouched: the old plan path still works and still hard-keeps the tape
    assert wipe.keep_reason("market_microstructure_1s.jsonl", scope="fly") == "MARKET_TAPE_1S"


def test_module_has_no_import_time_side_effects_and_is_not_referenced_at_boot():
    here = Path(__file__).parent
    for name in ("bot.py", "fly-entrypoint.sh", "Dockerfile", "fly.toml"):
        text = (here / name).read_text(encoding="utf-8", errors="ignore")
        assert "full_reset_no_archive" not in text and "full-reset-no-archive" not in text, name
    src = (here / "full_reset_no_archive.py").read_text(encoding="utf-8")
    assert "__main__" not in src  # no direct entry point: only clean_epoch_wipe.py --full-reset-no-archive


def test_fly_toml_flips_per_wipe_prefix():
    text = (Path(__file__).parent / "fly.toml").read_text(encoding="utf-8")
    assert 'RESEARCH_SEGMENTS_PREFIX_PER_WIPE = "1"' in text


def test_workflow_job_order_is_guarded_and_never_resumes_paper():
    text = (Path(__file__).resolve().parents[2] / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
    start = text.index("\n  full-reset-no-archive:\n")
    job = text[start:text.index("\n  repair-v22-seals:\n", start)]
    order = ["name: Plan (read-only)", "name: Require confirm token, written reason and a fresh volume snapshot",
             "name: Enter paper maintenance boundary", "name: Prove every relay is PAUSED, disarmed and flat",
             "name: Snapshot the live-off facts before the wipe", "name: In-process guarded reset",
             "name: Stamp the fresh-start epoch", "name: File wipe, intent receipt first",
             "name: Restart the machine", "name: Verify (live off unchanged"]
    positions = [job.index(marker) for marker in order]
    assert positions == sorted(positions), "the guarded order changed"
    assert "full-reset-no-archive-plan" in text and "full-reset-no-archive-execute" in text
    assert "/api/resume" not in job and "fly_failure_paper_resume" not in job and "fly_postdeploy_active_gate" not in job
    assert "DELETE-FULL-RESET:" in job and "BREAK_21_DAY_RESEARCH_FREEZE" in job and "full_reset_reason" in job
    assert "--full-reset-no-archive" in job and "--expect-git-rev" in job
    assert "aws " not in job and "rclone" not in job and "s3" not in job.lower()

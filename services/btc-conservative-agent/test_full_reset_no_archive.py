"""full-reset-no-archive: explicit KEEP allowlist, fail-closed pre-checks, receipt-first, idempotent, never at boot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

import clean_epoch_wipe as wipe
import full_reset_no_archive as frna
import research_freeze

REV = "5164548a1234"
FLAGS = {"live_armed": False, "bitfinex_live_enabled": False, "force_paper_mode": True, "live_eligible_switch": 0,
         "tile_enabled": {"FAMILY_DISLOC_BFX_DISLOCATION_1M": False, "FAMILY_DISLOC_FOI_LONG_1M": False}}
HEALTH = {"execution_paused": True, "pause_owner": "DEPLOY_MAINTENANCE", "live_armed": False,
          "bitfinex_live_enabled": False, "force_paper_mode": True, "source_git_rev": REV,
          }
ENV = {"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1"}
def _proof(**kw):
    """The workflow's fresh private exposure-audit proof (/health carries NO exchange facts)."""
    base = {"exchange_positions": 0, "exchange_active_orders": 0, "exchange_delta_btc": 0.0,
            "authoritative": True, "fresh": True, "flat": True, "error": None, "proof_ts": time.time()}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not _GONE}


_GONE = object()
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
    _w(rt / "sub" / "unknown.dat")  # unclassified (not top level, not a data stream)
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
        "lane_pnl_ledger.json": d, "paper_delay_pool.json": d, "xvp_shadow_signals.jsonl": d, "tierA/bitfinex_l1_tape_1s/v1/p.parquet": d,
        "never_heard_of_it.jsonl": d, "never_heard_of_it.jsonl.1": d,
        "mystery_blob.bin": u, "sub/unknown.dat": u,
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
    assert {u["rel"] for u in plan["unclassified_retained"]} == {"mystery_blob.bin", "sub/unknown.dat"}
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
                "mystery_blob.bin", "sub/unknown.dat", "forward-tracker/chain.jsonl"):
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


def test_a_file_still_being_appended_is_deleted_and_plan_identity_ignores_sizes_and_new_files(vol):
    rt = vol / "runtime"
    plan = _plan(vol)
    with open(rt / "bfx_trades.jsonl", "a") as fh:  # a collector appends after planning
        fh.write("late row\n")
    assert _plan(vol)["plan_sha256"] == plan["plan_sha256"]
    done = frna.execute(plan, receipts_dir=rt / frna.RECEIPTS_DIRNAME, git_rev=REV, reason="full reset no archive",
                        freeze_override=OVERRIDE)
    assert done["completed"] is True and not (rt / "bfx_trades.jsonl").exists()
    (rt / "brand_new_rotation.jsonl.9").write_text("x")  # rotation / new copies must NOT change the sha (rules identity)
    assert _plan(vol)["plan_sha256"] == plan["plan_sha256"]


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
                freeze_override_reason=OVERRIDE["reason"], exchange_proof_json=json.dumps(_proof()))
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


# ---------------------------------------------------------------- HM review changes (17 Oct)

ABSENT_KEYS = ("exchange_positions", "exchange_active_orders", "exchange_delta_btc")


def test_health_carries_no_exchange_facts_so_no_proof_means_abort(vol):
    assert _run(_good(vol, exchange_proof_json="")) == 7
    assert _nothing_deleted(vol)
    assert not (vol / "runtime" / frna.RECEIPTS_DIRNAME).exists()


@pytest.mark.parametrize("key", ABSENT_KEYS)
def test_missing_exchange_key_in_the_proof_aborts_without_deleting(vol, key):
    assert _run(_good(vol, exchange_proof_json=json.dumps(_proof(**{key: _GONE})))) == 7
    assert _nothing_deleted(vol)
    assert not (vol / "runtime" / frna.RECEIPTS_DIRNAME).exists()


@pytest.mark.parametrize("key", ABSENT_KEYS)
@pytest.mark.parametrize("bad", [None, "0", True, 2, [1]])
def test_exchange_key_must_be_a_real_zero(vol, key, bad):
    assert _run(_good(vol, exchange_proof_json=json.dumps(_proof(**{key: bad})))) == 7
    assert _nothing_deleted(vol)


@pytest.mark.parametrize("field", ["authoritative", "fresh", "flat"])
def test_exchange_proof_must_be_authoritative_fresh_and_flat(vol, field):
    assert _run(_good(vol, exchange_proof_json=json.dumps(_proof(**{field: False})))) == 7
    assert _nothing_deleted(vol)


@pytest.mark.parametrize("proof_ts", [None, time.time() - 3600, "x"])
def test_stale_or_undated_exchange_proof_aborts(vol, proof_ts):
    assert _run(_good(vol, exchange_proof_json=json.dumps(_proof(proof_ts=proof_ts)))) == 7
    assert _nothing_deleted(vol)


@pytest.mark.parametrize("raw", ["{not json", "[]", "null"])
def test_unparseable_exchange_proof_aborts(vol, raw):
    assert _run(_good(vol, exchange_proof_json=raw)) == 7
    assert _nothing_deleted(vol)


def test_all_keys_removed_lists_each_violation():
    health = dict(HEALTH)
    bad = frna.preflight_violations(health, lifecycle={"paper_only": True, "live_armed": False, "positions": [], "pending_orders": []},
                                    config={}, open_positions=[], expect_git_rev=REV, env=ENV)
    assert len([b for b in bad if "absent (cannot prove" in b]) == 3


def test_zero_forms_that_are_accepted():
    assert frna._is_real_zero(0) and frna._is_real_zero(0.0) and frna._is_real_zero([]) and frna._is_real_zero({})
    assert not any(frna._is_real_zero(v) for v in (None, "", "0", False, True, 1, 0.0003, [0], {"a": 1}))


def test_unreadable_config_aborts_without_deleting(vol):
    (vol / "runtime" / "config-7002.json").write_text("{not json")
    # the plan must be built AFTER corrupting so the sha matches; flag views simply skip the unreadable file
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


def test_missing_config_aborts_without_deleting(vol):
    (vol / "runtime" / "config-7002.json").unlink()
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


@pytest.mark.parametrize("content", ["not json", "null", '[{"id": 1}]', '{"T1": {}}'])
def test_open_positions_must_be_a_real_empty_collection(vol, content):
    (vol / "runtime" / "open_positions.json").write_text(content)
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


def test_missing_open_positions_file_aborts(vol):
    (vol / "runtime" / "open_positions.json").unlink()
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)


@pytest.mark.parametrize("name", ["orders.json", "positions.json", "pending_orders.json", "paper_state.json"])
def test_legacy_state_files_must_hold_no_rows(vol, name):
    (vol / "runtime" / name).write_text('[{"trade_id": "T9", "status": "CLOSED"}]')
    assert _run(_good(vol)) == 7 and _nothing_deleted(vol)
    (vol / "runtime" / name).write_text("[]")
    assert _run(_good(vol)) == 0  # empty is fine and is kept


def test_legacy_state_files_are_kept_and_never_written_by_the_bot():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent
    for name in ("orders.json", "positions.json", "pending_orders.json", "paper_state.json"):
        assert frna.classify(name)[0] == frna.KEEP
        writers = [p.name for p in root.glob("*.py") if not p.name.startswith("test_")
                   and p.name not in ("full_reset_no_archive.py", "research_reset_inventory.py")
                   and f'"{name}"' in p.read_text("utf-8", errors="replace")]
        assert writers == [], (name, writers)


def test_nested_market_datasets_and_named_ledgers_are_delete_class():
    for rel in ("cross_venue_tape_1m/2026-10-16/part-0.jsonl", "market_context_1m/p.parquet", "liquidations/x.jsonl.gz",
                "bitfinex_l1_tape_1s/v1/p.parquet", "sub/disloc_trades.jsonl", "lanes/L1/disloc_foi_trades.jsonl.2",
                "decisions_3factor.csv", "blocked_signals_3factor.csv", "pipeline_events_3factor.csv",
                "setup_log_3factor.csv", "candles_3factor.csv", "ai_errors_3factor.csv", "blocked_3factor.csv",
                "expired_orders_3factor.csv", "lane_lab_pnl_ledger.json", "lane_opportunity_capture.json",
                "trades_3factor.csv.validation.json"):
        assert frna.classify(rel)[0] == frna.DELETE, (rel, frna.classify(rel))
    assert frna.DELETE_TAPE_DATASETS == wipe.TAPE_DATASETS | wipe.MARKET_DATASETS


def test_execute_aborts_when_an_unclassified_data_file_exists(vol, capsys):
    (vol / "runtime" / "sub" / "unknown.jsonl").write_text("row\n")
    assert _run(_good(vol)) == 9
    assert "UNCLASSIFIED_DATA_FILES" in capsys.readouterr().out
    assert _nothing_deleted(vol)
    assert not (vol / "runtime" / frna.RECEIPTS_DIRNAME).exists()
    plan = _plan(vol)
    assert plan["unclassified_data_files_count"] == 1 and plan["unclassified_data_files"][0]["rel"] == "sub/unknown.jsonl"


def test_plan_sha_is_a_rules_hash_stable_across_rotation_and_in_process_reset(vol):
    rt = vol / "runtime"
    before = _plan(vol)["plan_sha256"]
    (rt / "trades_3factor.csv").unlink()  # the in-process reset removed some files
    (rt / "bfx_trades.jsonl").rename(rt / "bfx_trades.jsonl.7")  # rotation
    (rt / "new_stream_1.jsonl").write_text("x")
    assert _plan(vol)["plan_sha256"] == before
    assert _run(_good(vol, expect_plan_sha256=before, confirm=frna.confirm_token(before))) == 0


# ---------------------------------------------------------------- workflow order and restart/pause/shutdown contracts

WORKFLOW_TEXT = (Path(__file__).resolve().parents[2] / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
JOB = WORKFLOW_TEXT[WORKFLOW_TEXT.index("\n  full-reset-no-archive:"):WORKFLOW_TEXT.index("\n  repair-v22-seals:")]


def _step(name_fragment: str) -> str:
    start = JOB.index("- name: " + name_fragment)
    nxt = JOB.find("\n      - name:", start + 1)
    return JOB[start:nxt if nxt != -1 else len(JOB)]


def test_workflow_boundary_fails_closed_on_missing_keys():
    step = _step("Enter paper maintenance boundary")
    assert "def real_zero" in step and "not status.get(" not in step and "progress.get(\"open_positions\") ==" not in step
    assert 'real_zero(progress.get("open_positions"))' in step and 'real_zero(progress.get("pending_orders"))' in step
    # the exchange is proven by the private exposure audit, twice: at the boundary and right before the file wipe
    assert JOB.count("fly_full_reset_gate.py exchange-proof") == 2
    assert JOB.index("exchange-proof") < JOB.index("check-relay-flat.mjs")
    assert JOB.index("id: exchproof") < JOB.index("id: filewipe")
    assert "--exchange-proof-json" in _step("File wipe, intent receipt first") and "EXCHANGE_PROOF" in _step("File wipe, intent receipt first")


def test_workflow_restart_always_runs_after_the_first_delete_step_and_stays_paused():
    step = _step("Restart the machine")
    assert "always()" in step and "steps.inproc.conclusion != 'skipped'" in step
    assert "continue-on-error" not in JOB  # a failed wipe still fails the job
    assert "pause_owner" in step and "DEPLOY_MAINTENANCE" in step and "execution_paused" in step
    assert "/api/resume" not in JOB and "api/resume" not in step
    assert JOB.index("id: inproc") < JOB.index("id: restart") and JOB.index("id: filewipe") < JOB.index("id: restart")


def test_workflow_post_restart_plan_and_dv_assertions_precede_the_end():
    post = _step("Post-restart read-only plan")
    assert "always()" in post and "steps.restart.conclusion == 'success'" in post
    assert "clean_epoch_wipe.py plan" in post and "clean_epoch_wipe.py execute" not in post and "check-leftovers" in post
    verify = _step("Verify (live off unchanged")
    assert "assert-dv" in verify and "bfx_book_10l_1s.jsonl" in verify and "wait-book" in verify
    assert JOB.index("Post-restart read-only plan") < JOB.index("Verify (live off unchanged")


def test_the_pause_owner_is_persisted_and_restored_so_paper_cannot_resume_after_restart(tmp_path, monkeypatch):
    import bot
    monkeypatch.chdir(tmp_path)
    assert "manual_admin_pause" in bot._persistent_config_keys() and "pause_intent" in bot._persistent_config_keys()
    saved = {k: bot.state.get(k) for k in ("manual_admin_pause", "pause_intent", "execution_paused", "execution_reason", "_pause_priority")}
    try:
        with bot.state_lock:
            bot.state["manual_admin_pause"] = True
            bot.state["pause_intent"] = bot.PAUSE_OWNER_DEPLOY_MAINTENANCE
        bot.save_persistent_config()
        with bot.state_lock:  # a fresh process: nothing in memory
            for k in ("manual_admin_pause", "pause_intent", "execution_paused"):
                bot.state[k] = None if k == "pause_intent" else False
        bot.load_persistent_config()
        assert bot.state["execution_paused"] is True and bot.state["pause_intent"] == "DEPLOY_MAINTENANCE"
        with bot.state_lock:
            assert bot._pause_owner_locked() == "DEPLOY_MAINTENANCE"
    finally:
        with bot.state_lock:
            bot.state.update(saved)


def test_sigterm_shutdown_never_rewrites_the_lane_ledger_or_the_trade_csv_from_memory():
    """After the in-process reset the in-memory books are empty; a SIGTERM rewrite of lane_pnl_ledger.json or the trade
    CSV from memory would put back wiped state. The shutdown handler and the supervisor's shutdown branch may only write
    open_positions.json, paper_lifecycle_v1.json, the config and replay dumps (no ledger / trade-row writers)."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    forbidden = {"_write_lane_pnl_ledger_file", "log_trade", "dynamic_csv_writer", "update_lane_pnl_ledger", "to_csv",
                 "_record_lane_ledger", "append_trade_row"}

    def calls(node):
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
        return out

    assert not (calls(funcs["shutdown_handler"]) & forbidden)
    main = funcs["main"]
    branch = [n for n in ast.walk(main) if isinstance(n, ast.If) and "shutdown_event.wait" in ast.unparse(n.test)]
    assert branch, "supervisor shutdown branch not found"
    shutdown_calls = set().union(*(calls(b) for b in branch))
    assert not (shutdown_calls & forbidden), shutdown_calls & forbidden
    assert {"save_positions", "save_persistent_config"} <= shutdown_calls
    # the only writers reachable from save_positions are the positions file and the paper lifecycle snapshot
    assert not (calls(funcs["save_positions"]) & forbidden) and not (calls(funcs["save_paper_lifecycle"]) & forbidden)


def test_the_workflow_step_names_are_plain_scalars_and_in_the_guarded_order():
    """No PyYAML in CI: a ': ' inside an unquoted step name breaks the whole workflow file, so pin that textually."""
    import re
    names = re.findall(r"^      - name: (.+)$", JOB, flags=re.M)
    assert names and all(": " not in n and not n.startswith(("'", '"', "[", "{", "&", "*", "!", "|", ">", "%", "@", "`")) or
                         (n[0] in "'\"" and n[-1] == n[0]) for n in names), [n for n in names if ": " in n]
    order = ["Plan (read-only)", "Require confirm token", "Enter paper maintenance boundary", "Prove the exchange is flat",
             "Prove every relay", "Snapshot the live-off facts", "In-process guarded reset", "Stamp the fresh-start epoch",
             "Re-prove the exchange is flat", "File wipe", "Restart the machine", "Post-restart read-only plan", "Verify"]
    idx = [next(i for i, n in enumerate(names) if n.startswith(frag)) for frag in order]
    assert idx == sorted(idx), (order, idx)


@pytest.mark.parametrize("err", ["boom", "", 0, _GONE])
def test_exchange_proof_error_must_be_present_and_null(vol, err):
    assert _run(_good(vol, exchange_proof_json=json.dumps(_proof(error=err)))) == 7
    assert _nothing_deleted(vol)


# ---------------------------------------------------------------- deny-list (HM 20:16)

DENIED = ["live_flag.csv", "tile_switch.jsonl", "settings.json", "settings.csv", "armed_state.jsonl", "my_flags.jsonl.1",
          "tile_7_ledger.csv", "live_orders.jsonl.gz", "switch_history.csv", "ui_settings.jsonl", "sub/live_rows.jsonl"]


@pytest.mark.parametrize("rel", DENIED)
def test_denylisted_names_are_never_delete(rel):
    action, why = frna.classify(rel)
    assert action != frna.DELETE, (rel, action, why)


def test_denylisted_top_level_data_file_blocks_execute_and_is_listed(vol, capsys):
    for name in ("live_flag.csv", "tile_switch.jsonl", "settings.json"):
        (vol / "runtime" / name).write_text("x\n")
    plan = _plan(vol)
    cands = {c["rel"] for c in plan["candidates"]}
    assert not cands & {"live_flag.csv", "tile_switch.jsonl", "settings.json"}
    assert {"live_flag.csv", "tile_switch.jsonl"} <= {u["rel"] for u in plan["unclassified_data_files"]}
    assert {"live_flag.csv", "tile_switch.jsonl", "settings.json"} <= {u["name"] for u in plan["top_level_denied_or_unclassified"]}
    assert _run(_good(vol)) == 9 and "UNCLASSIFIED_DATA_FILES" in capsys.readouterr().out
    assert _nothing_deleted(vol)
    for name in ("live_flag.csv", "tile_switch.jsonl", "settings.json"):
        assert (vol / "runtime" / name).exists()


def test_denylist_does_not_swallow_the_real_delete_classes():
    for rel in frna.DELETE_EXACT_NAMES:
        assert not any(t in rel for t in frna.DENY_NAME_TOKENS), rel  # no explicit DELETE name is denied
        assert frna.classify(rel)[0] == frna.DELETE, rel


def test_rules_version_is_bumped_and_changes_the_plan_sha(vol, monkeypatch):
    assert frna.RULES_VERSION == "full-reset-rules-v7-fly-analyzer-mirror"
    before = _plan(vol)["plan_sha256"]
    monkeypatch.setattr(frna, "DENY_NAME_TOKENS", ("live", "arm"))
    assert _plan(vol)["plan_sha256"] != before


def test_plan_file_lists_top_level_names_counts_and_bytes(vol):
    plan = _plan(vol)
    rows = {r["name"]: r for r in plan["top_level_delete"]}
    assert rows["trades_3factor.csv"]["files"] == 2  # the file and its .1 rotation folded together
    assert rows["trades_3factor.csv"]["bytes"] > 0 and all(set(r) == {"name", "files", "bytes"} for r in rows.values())
    assert "sub/unknown.dat" not in rows and "research.db" not in rows
    s = frna.summary(plan)
    assert s["top_level_delete"] == plan["top_level_delete"] and "candidates" not in s


def test_safety_net_is_the_last_step_pauses_then_stops_the_machine_if_not_proven():
    import re
    names = re.findall(r"^      - name: (.+)$", JOB, flags=re.M)
    assert names[-1].startswith("Safety net on failure")
    step = _step("Safety net on failure")
    assert "failure()" in step and "full-reset-no-archive-execute" in step
    assert '"/api/pause"' in step and "DEPLOY_MAINTENANCE" in step
    assert 'pause_owner") == "DEPLOY_MAINTENANCE"' in step
    assert '"flyctl", "machine", "stop"' in step
    assert step.index("/api/pause") < step.index('"machine", "stop"')
    assert "/api/resume" not in step and "continue-on-error" not in step
    assert "FLY_API_TOKEN" in step and "BOT_ADMIN_TOKEN" in step  # secrets only via env, never echoed
    assert "echo $BOT_ADMIN_TOKEN" not in step and "print(os.environ" not in step


def test_tile_integrity_jsonl_is_the_one_deny_list_override():
    assert frna.DENY_OVERRIDE_DELETE_NAMES == frozenset({"tile_integrity.jsonl"})
    assert frna.classify("tile_integrity.jsonl")[0] == frna.DELETE
    assert frna.classify("tile_integrity.jsonl.1")[0] == frna.DELETE
    assert frna.classify("tile_integrity.jsonl.3.gz")[0] == frna.DELETE
    # nothing else benefits: other tile / switch names, nested copies and near-misses stay denied
    for rel in ("tile_integrity.csv", "tile_integrity_report.jsonl", "my_tile_integrity.jsonl", "sub/tile_integrity.jsonl",
                "tile_switch.jsonl", "live_flag.csv", "tile_independence_report.json", "settings.json"):
        assert frna.classify(rel)[0] != frna.DELETE, rel


def test_tile_integrity_jsonl_no_longer_blocks_execute(vol):
    (vol / "runtime" / "tile_integrity.jsonl").write_text('{"x":1}\n')
    plan = _plan(vol)
    assert "tile_integrity.jsonl" in {c["rel"] for c in plan["candidates"]}
    assert plan["unclassified_data_files_count"] == 0
    assert _run(_good(vol)) == 0
    assert not (vol / "runtime" / "tile_integrity.jsonl").exists()


# ------------------------------------------------------------------ N1: analyzer-derived reports (R5 fresh-only)

# Analyzer *.json reports that were UNCLASSIFIED (retained) under full-reset-rules-v4 and are DELETE from v5.
# The two sidecars are the HM N1 items; the rest is the inventory of the shared analyzer report list plus extras.
ANALYZER_DEPTHS = ("analyzer/{}", "research/analyzer/{}", "runtime_mirror/x/analyzer/{}")
# Still retained on purpose: manifests / pointers / snapshots / the startup sync gate (UNCLASSIFIED, .json so they do
# not block execute) and deny-listed names (owner decision: the deny-list is never overridden by a DELETE rule).
# HM #850 R2 C2: the four analyzer run manifests moved to DELETE (ANALYZER_MANIFESTS_DELETE below).
ANALYZER_MANIFESTS_DELETE = (
    "report_manifest.json", "policy_evidence_library_manifest.json", "safe_policy_genome_v3_exhaustive_manifest.json",
    "past_analysis_manifest.json")
ANALYZER_RETAINED_UNCLASSIFIED = (
    "manifest.json", "canonical_dataset_current.json", "fly_runtime_snapshot_v1.json",
    "snapshot_meta.json", "verification_index.json", "bot_analyzer_sync.json")
# RK ruling (HM #850 R2 C4(a)): the deny-list protects PRIMARY tile / live / switch files only; the analyzer's derived
# tile_* report copies are DELETE (v7). shadow_vs_live_entry_report.json is not a declared analyzer report: still denied.
ANALYZER_TILE_COPIES_NOW_DELETE = ("tile_evidence_points_report.json", "tile_paired_comparison_report.json")
ANALYZER_DENYLISTED = ("shadow_vs_live_entry_report.json",)


def test_n1_entry_baseline_sidecars_are_delete_at_any_depth():
    for tmpl in ANALYZER_DEPTHS:
        assert frna.classify(tmpl.format("entry_baseline_replay_report.json")) == (frna.DELETE, "ANALYZER_DERIVED_REPORT")
        assert frna.classify(tmpl.format("entry_baseline_replay_episode_receipts.jsonl.gz")) == (
            frna.DELETE, "ANALYZER_EPISODE_RECEIPTS")
        # HM #850 R2 C5: an unknown producer's shard is NOT deleted by suffix; it stays UNCLASSIFIED and blocks execute
        for shard in ("foo_episode_receipts.jsonl.gz", "episode_receipts.jsonl.gz", "x_episode_receipts.jsonl.gz"):
            assert frna.classify(tmpl.format(shard)) == (frna.UNCLASSIFIED, "NOT_ALLOWLISTED_NOT_A_DATA_CLASS"), shard
            assert frna.is_data_file(tmpl.format(shard))
    assert frna.ANALYZER_EPISODE_RECEIPTS_FILES == {"entry_baseline_replay_episode_receipts.jsonl.gz"}
    # only inside an analyzer/ directory: a top-level or foreign-dir copy keeps its v4 classification
    assert frna.classify("entry_baseline_replay_report.json")[0] == frna.UNCLASSIFIED
    assert frna.classify("other/entry_baseline_replay_report.json")[0] == frna.UNCLASSIFIED
    assert frna.classify("other/x_episode_receipts.jsonl.gz")[0] == frna.UNCLASSIFIED
    assert frna.classify("analyzer_old/x_episode_receipts.jsonl.gz")[0] == frna.UNCLASSIFIED
    assert frna.classify("analyzer/x_episode_receipts.jsonl")[0] == frna.UNCLASSIFIED  # only the .jsonl.gz shards


def test_n1_every_shared_analyzer_report_is_delete_unless_an_earlier_rule_wins():
    import research_reset_inventory as rri
    assert frna.ANALYZER_DERIVED_REPORT_NAMES >= rri.ANALYZER_REPORT_FILES | rri.ANALYZER_SIDECAR_REPORT_FILES
    assert not frna.ANALYZER_DERIVED_REPORT_NAMES & set(ANALYZER_RETAINED_UNCLASSIFIED)
    for name in sorted(frna.ANALYZER_DERIVED_REPORT_NAMES):
        for tmpl in ANALYZER_DEPTHS:
            action, reason = frna.classify(tmpl.format(name))
            if name in frna.ANALYZER_EPISODE_RECEIPTS_FILES:
                assert (action, reason) == (frna.DELETE, "ANALYZER_EPISODE_RECEIPTS"), name
            else:  # v7: deny-list and RELAY no longer win over a declared analyzer report (RK C4(a)/(b))
                assert (action, reason) == (frna.DELETE, "ANALYZER_DERIVED_REPORT"), (tmpl.format(name), reason)


def test_n1_extra_derived_reports_are_real_analyzer_outputs():
    import re
    here = Path(__file__).resolve().parent
    src = (here / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    src += (here / "research" / "past_analysis.py").read_text(encoding="utf-8")
    written = set(re.findall(r'"([a-z0-9_]+\.json)"', src))
    assert frna.ANALYZER_EXTRA_DERIVED_REPORT_NAMES <= written


def test_n1_previously_unclassified_inventory_is_now_delete_and_the_rest_is_pinned():
    for name in ANALYZER_DENYLISTED:
        assert frna.classify("analyzer/" + name) == (frna.UNCLASSIFIED, "DENYLIST_NAME_NEVER_DELETE"), name
    for name in ANALYZER_TILE_COPIES_NOW_DELETE:
        assert frna.classify("analyzer/" + name) == (frna.DELETE, "ANALYZER_DERIVED_REPORT"), name
    for name in ANALYZER_RETAINED_UNCLASSIFIED:
        assert frna.classify("analyzer/" + name)[0] == frna.UNCLASSIFIED, name
    for name in frna.ANALYZER_EXTRA_DERIVED_REPORT_NAMES:
        assert frna.classify("analyzer/" + name) == (frna.DELETE, "ANALYZER_DERIVED_REPORT"), name
    assert frna.ANALYZER_DERIVED_MANIFEST_NAMES == set(ANALYZER_MANIFESTS_DELETE)
    for name in ANALYZER_MANIFESTS_DELETE:
        for tmpl in ANALYZER_DEPTHS:
            assert frna.classify(tmpl.format(name)) == (frna.DELETE, "ANALYZER_DERIVED_MANIFEST"), name
        assert frna.classify(name)[0] == frna.UNCLASSIFIED  # only inside an analyzer/ directory
        assert frna.classify("other/" + name)[0] == frna.UNCLASSIFIED
    # KEEP rules still win inside analyzer/
    assert frna.classify("analyzer/state.json")[0] == frna.KEEP
    assert frna.classify("analyzer/pathway_lane_specs.json")[0] == frna.KEEP
    assert frna.classify("analyzer/research_session.json")[0] == frna.KEEP
    assert frna.classify("research_reset_receipts/analyzer/entry_baseline_replay_report.json")[0] == frna.KEEP


def test_n1_plan_deletes_analyzer_reports_and_receipt_shards_and_unblocks_execute(vol, capsys):
    rt = vol / "runtime"
    deleted = ("analyzer/entry_baseline_replay_report.json", "analyzer/entry_baseline_replay_episode_receipts.jsonl.gz",
               "research/analyzer/entry_baseline_replay_episode_receipts.jsonl.gz", "analyzer/fast_cut_sweep_report.json",
               "deep/a/analyzer/historical_trade_cohort_report.json", "analyzer/report_manifest.json")
    retained = ("analyzer/manifest.json", "analyzer/shadow_vs_live_entry_report.json")
    for rel in deleted + retained:
        _w(rt / rel, "{}\n")
    plan = _plan(vol)
    cands = {c["rel"]: c["reason"] for c in plan["candidates"]}
    for rel in deleted:
        assert cands.get(rel, "").startswith("ANALYZER_"), rel
    assert not set(retained) & set(cands)
    assert {u["rel"] for u in plan["unclassified_retained"]} >= set(retained)
    # the .jsonl.gz shard was an UNCLASSIFIED data file under v4 (execute aborted); now nothing analyzer-side blocks
    assert not [u for u in plan["unclassified_data_files"] if "analyzer/" in u["rel"]]
    assert plan["delete_by_class"]["ANALYZER_EPISODE_RECEIPTS"]["files"] == 2
    assert plan["delete_by_class"]["ANALYZER_DERIVED_REPORT"]["files"] == 3
    assert plan["delete_by_class"]["ANALYZER_DERIVED_MANIFEST"]["files"] == 1
    assert _run(_good(vol)) == 0
    for rel in deleted:
        assert not (rt / rel).exists(), rel
    for rel in retained:
        assert (rt / rel).exists(), rel


def test_n1_analyzer_rules_change_the_plan_sha(vol, monkeypatch):
    before = _plan(vol)["plan_sha256"]
    monkeypatch.setattr(frna, "ANALYZER_DERIVED_REPORT_NAMES", frozenset({"x.json"}))
    assert _plan(vol)["plan_sha256"] != before


def test_c5_unknown_receipt_shard_blocks_execute(vol, capsys):
    """HM #850 R2 C5: a shard from an unknown producer is never deleted by suffix; it blocks execute until classified."""
    _w(vol / "runtime" / "analyzer" / "mystery_episode_receipts.jsonl.gz", "x")
    plan = _plan(vol)
    assert "analyzer/mystery_episode_receipts.jsonl.gz" in {u["rel"] for u in plan["unclassified_data_files"]}
    assert _run(_good(vol)) == 9
    assert (vol / "runtime" / "analyzer" / "mystery_episode_receipts.jsonl.gz").exists()


# ------------------------------------------------------------------ HM #850 R2 C3 / RK ruling C4(a)(b)

def test_c4a_deny_list_protects_primary_tile_files_by_full_path_only():
    # primary tile / live / switch files: protected wherever they are, including an undeclared name in analyzer/
    for rel in ("tile_switches.json", "tile_6_state.json", "tiles/tile_7_switch.json", "live_flags.json",
                "tile_evidence_points_report.json", "reports/tile_paired_comparison_report.json",
                "analyzer_old/tile_evidence_points_report.json", "analyzer/tile_switch.json", "analyzer/live_arm.json"):
        assert frna.classify(rel) == (frna.UNCLASSIFIED, "DENYLIST_NAME_NEVER_DELETE"), rel
    # derived copies of declared analyzer reports inside analyzer/ at any depth: DELETE
    for tmpl in ANALYZER_DEPTHS:
        for name in ANALYZER_TILE_COPIES_NOW_DELETE:
            assert frna.classify(tmpl.format(name)) == (frna.DELETE, "ANALYZER_DERIVED_REPORT")
    # the top-level owner override is unchanged
    assert frna.classify("tile_integrity.jsonl")[0] == frna.DELETE
    # KEEP rules still beat the analyzer rule (allowlist, config, preserved research)
    assert frna.classify("analyzer/state.json")[0] == frna.KEEP


def test_c4b_bitfinex_named_reports_are_analyzer_derived_with_file_line_evidence(monkeypatch):
    import re
    here = Path(__file__).resolve().parent
    src = (here / "analyzer_research_engine_v62.py").read_text(encoding="utf-8").splitlines()
    consts = {"BITFINEX_COPY_FIDELITY_REPORT_FILE": "bitfinex_copy_fidelity_lifecycles_report.json",
              "COPY_ONLY_EXCHANGE_FILLS_REPORT_FILE": "copy_only_exchange_fills_report.json"}
    for const, fname in consts.items():
        assert any(line.startswith(f'{const} = "{fname}"') for line in src), const
        # the only use besides the report lists is the cohort split writer that writes via analyzer_report_path
        assert any(re.search(rf"\(\w+, {const},", line) for line in src), const
    body = "\n".join(src)
    writer = body[body.index("def _write_cohort_report"):body.index("def research_cohort_split_reports")]
    assert "analyzer_report_path(filename)" in writer and 'open(path, "w"' in writer
    # no other writer anywhere in the repo (py / ts / js / ps1)
    named = set()
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.ts", "*.js", "*.ps1"], cwd=REPO_ROOT, check=True,
                             capture_output=True, text=True).stdout.split()
    for p in (REPO_ROOT / t for t in tracked):
        if "node_modules" not in p.parts and not p.name.startswith("test_"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(f in text for f in consts.values()):
                named.add(p.name)
    assert named == {"analyzer_research_engine_v62.py", "research_reset_inventory.py", "full_reset_no_archive.py"}
    assert frna.ANALYZER_RELAY_NAMED_DERIVED_REPORTS == set(consts.values())
    for fname in consts.values():
        for tmpl in ANALYZER_DEPTHS:
            assert frna.classify(tmpl.format(fname)) == (frna.DELETE, "ANALYZER_DERIVED_REPORT")
        assert frna.classify(fname)[0] == frna.KEEP  # outside analyzer/ the RELAY rule still keeps it
    assert frna.classify("analyzer/bitfinex_fill_audit.json") == (frna.KEEP, "RELAY_BITFINEX_EVIDENCE")
    # switchable spot: out of the set, the RELAY KEEP rule applies again
    monkeypatch.setattr(frna, "ANALYZER_RELAY_NAMED_DERIVED_REPORTS", frozenset())
    assert frna.classify("analyzer/copy_only_exchange_fills_report.json") == (frna.KEEP, "RELAY_BITFINEX_EVIDENCE")


REPO_ROOT = Path(__file__).resolve().parents[2]
_BOT_MIRROR_FUNCS = ("_data_sync_runtime_root", "_data_sync_volume_root", "_analyzer_mirror_dir",
                     "_analyzer_generations_dir", "_analyzer_current_pointer_path", "_valid_analyzer_generation",
                     "_recover_latest_analyzer_generation", "_active_analyzer_mirror_dir",
                     "_analyzer_bundle_fresh_gate")


def _bot_mirror_namespace():
    """The bot's own resolver functions (AST-extracted from bot.py, as test_fly_data_sync_contract does)."""
    import ast
    import os
    import re
    tree = ast.parse((Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8"))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _BOT_MIRROR_FUNCS]
    assert {n.name for n in selected} == set(_BOT_MIRROR_FUNCS)
    ns = {"Path": Path, "os": os, "json": json, "re": re, "hashlib": hashlib,
          "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json", "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
          "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
          "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024, "_ANALYZER_BUNDLE_FRESH_FLOOR_TS": 1792263671,
          "datetime": __import__("datetime").datetime}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def _fly_mirror(vol: Path) -> Path:
    files = {"analysis_dashboard.html": b"<html>dash</html>",
             "reports/tile_paired_comparison_report.json": b'{"tile": 6}',
             "reports/tile_evidence_points_report.json": b'{"tile": 7}',
             "report_manifest.json": b'{"fresh_epoch": {"cutoff_utc": "2026-10-17T19:01:11+00:00"}}'}
    gen = vol / "analyzer_generations" / "generation-20261024T000000Z-abc"
    for rel, data in files.items():
        (gen / rel).parent.mkdir(parents=True, exist_ok=True)
        (gen / rel).write_bytes(data)
    _w(gen / "bundle_manifest.json", json.dumps({"schema": "analyzer_mirror_bundle_v2", "files": [
        {"path": rel, "sha256": hashlib.sha256(d).hexdigest(), "size_bytes": len(d)} for rel, d in files.items()]}))
    _w(gen / "status.json", json.dumps({"complete": True, "schema": "analyzer_mirror_bundle_v2"}))
    _w(vol / "analyzer_generations" / ".staging-x" / "partial.json", "{}")
    _w(vol / "analyzer_current.json", json.dumps({"generation": gen.name}))
    _w(vol / "analyzer_mirror" / "analysis_dashboard.html", "legacy")
    _w(vol / "analyzer_mirror" / "reports" / "tile_evidence_points_report.json", "{}")
    _w(vol / "analyzer_mirror" / "live_flags.json", "{}")  # a mirror copy: deleted in full, deny-list does not apply
    return gen


def _other_data(vol: Path) -> dict:
    """Folders / files on the volume outside runtime/ that are NOT in the Fly analyzer scope."""
    rels = ("segment-store/a.jsonl", "analyzer_mirror_backup/x.json", "analyzer/report_manifest.json",
            "analyzer_current.json.bak", "other/analyzer_generations/g/x.json", "top_level.jsonl", "v3/ledger.jsonl")
    out = {}
    for rel in rels:
        _w(vol / rel, f"keep {rel}\n")
        out[rel] = _sha(vol / rel)
    return out


def test_c3_plan_lists_exactly_the_three_fly_scopes_and_nothing_else_outside_runtime(vol):
    _fly_mirror(vol)
    _other_data(vol)
    plan = _plan(vol)
    root = vol.as_posix()
    assert plan["outside_runtime_delete_scopes"] == [f"{root}/analyzer_mirror/**", f"{root}/analyzer_generations/**",
                                                     f"{root}/analyzer_current.json"]
    outside = [c for c in plan["candidates"] if not c["path"].startswith(str(vol / "runtime") + "/")]
    assert outside and all(c["rel"].startswith(frna.FLY_SCOPE_PREFIX) for c in outside)
    for c in outside:
        assert (c["path"].startswith((f"{root}/analyzer_mirror/", f"{root}/analyzer_generations/"))
                or c["path"] == f"{root}/analyzer_current.json"), c["path"]
    rels = {c["rel"] for c in outside}
    assert "@data/analyzer_current.json" in rels
    assert "@data/analyzer_mirror/reports/tile_evidence_points_report.json" in rels  # tile copy: DELETE
    assert "@data/analyzer_mirror/live_flags.json" in rels
    assert "@data/analyzer_generations/generation-20261024T000000Z-abc/reports/tile_paired_comparison_report.json" in rels
    assert "@data/analyzer_generations/.staging-x/partial.json" in rels
    assert len(rels) == 11  # 6 generation files + 1 staging + 3 legacy mirror + the pointer
    assert {c["reason"] for c in outside} == {"FLY_ANALYZER_MIRROR", "FLY_ANALYZER_GENERATION",
                                              "FLY_ANALYZER_CURRENT_POINTER"}
    assert plan["delete_by_class"]["FLY_ANALYZER_CURRENT_POINTER"]["files"] == 1
    assert not [u for u in plan["unclassified_retained"] if u["rel"].startswith("@data/")]


def test_c3_execute_removes_the_mirror_reads_back_and_bot_resolver_returns_none(vol, monkeypatch, capsys):
    gen = _fly_mirror(vol)
    others = _other_data(vol)
    monkeypatch.setenv("BOT_DATA_DIR", str(vol))
    bot = _bot_mirror_namespace()
    assert bot["_analyzer_generations_dir"]() == vol.resolve() / "analyzer_generations"
    assert bot["_active_analyzer_mirror_dir"]() == gen.resolve()  # the live mirror resolves before the reset
    assert _run(_good(vol)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fly_analyzer_readback"]["ok"] is True and out["verify"]["fly_analyzer_readback"]["ok"] is True
    assert out["completed"] is True
    for d in ("analyzer_mirror", "analyzer_generations"):
        assert [p for p in (vol / d).rglob("*") if not p.is_dir()] == []
        assert [p for p in (vol / d).iterdir()] == []  # empty sub-directories pruned too
    assert not (vol / "analyzer_current.json").exists()
    for rel, digest in others.items():  # every other /app/data folder untouched
        assert _sha(vol / rel) == digest, rel
    assert bot["_active_analyzer_mirror_dir"]() is None
    assert bot["_recover_latest_analyzer_generation"]() is None
    receipt = json.loads(next((vol / "runtime" / frna.RECEIPTS_DIRNAME).glob("*/receipt-complete.json")).read_text())
    assert receipt["fly_analyzer_readback"]["ok"] is True and receipt["completed"] is True


def test_c3_readback_failure_records_completed_false(vol, capsys):
    _fly_mirror(vol)
    def unlink(path):
        if "/analyzer_generations/" not in path:
            os.unlink(path)
    rc = frna.run_cli(_good(vol), fetch=lambda url: dict(HEALTH), env=ENV, unlink=unlink)
    assert rc == 9
    assert "FLY_ANALYZER_READBACK_FAILED" in capsys.readouterr().out
    receipt = json.loads(next((vol / "runtime" / frna.RECEIPTS_DIRNAME).glob("*/receipt-complete.json")).read_text())
    rb = receipt["fly_analyzer_readback"]
    assert receipt["completed"] is False and rb["leftover_file_count"] == 7
    assert rb["leftover_count"] == 7 + rb["leftover_kinds"]["dir"] and rb["leftover_kinds"]["dir"] >= 1
    assert frna.completed_receipt(vol / "runtime" / frna.RECEIPTS_DIRNAME, receipt["plan_sha256"]) is None


def test_c3_symlinked_scope_root_is_never_followed(vol, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    _w(outside / "precious.json", "{}")
    os.symlink(outside, vol / "analyzer_mirror")
    os.symlink(outside / "precious.json", vol / "analyzer_current.json")
    plan = _plan(vol)
    assert not [c for c in plan["candidates"] if c["rel"].startswith("@data/")]
    assert (outside / "precious.json").exists()


def test_c3_fly_scope_rules_change_the_plan_sha(vol, monkeypatch):
    before = _plan(vol)["plan_sha256"]
    monkeypatch.setattr(frna, "FLY_ANALYZER_MIRROR_DIRS", ("analyzer_mirror",))
    assert _plan(vol)["plan_sha256"] != before


# ---- HM #850 R2 C3b: read-back fails on a symlinked scope root or ANY leftover entry; '..' never in scope


def test_c3b_readback_ok_only_when_the_scopes_are_truly_empty(vol):
    for d in frna.FLY_ANALYZER_MIRROR_DIRS:
        (vol / d).mkdir(parents=True, exist_ok=True)
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is True and rb["leftover_count"] == 0 and rb["problems"] == [] and rb["pointer_present"] == []
    assert frna.fly_analyzer_readback(vol / "absent")["ok"] is True  # no scopes at all is also clean


def test_c3b_readback_fails_on_a_leftover_empty_directory(vol):
    (vol / "analyzer_generations" / "generation-x" / "reports").mkdir(parents=True)
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is False and rb["leftover_file_count"] == 0
    assert {e["rel"] for e in rb["leftover_entries"]} == {"@data/analyzer_generations/generation-x",
                                                          "@data/analyzer_generations/generation-x/reports"}
    assert rb["leftover_kinds"] == {"dir": 2}


def test_c3b_readback_fails_on_leftover_symlinks_without_following_them(vol, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-c3b")
    _w(outside / "precious.json", "{}")
    (vol / "analyzer_mirror").mkdir(parents=True)
    os.symlink(outside, vol / "analyzer_mirror" / "dir_link")
    os.symlink(outside / "gone.json", vol / "analyzer_mirror" / "dangling.json")
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is False and rb["leftover_kinds"] == {"symlink": 2}
    assert "@data/analyzer_mirror/dir_link/precious.json" not in {e["rel"] for e in rb["leftover_entries"]}
    assert (outside / "precious.json").exists()


def test_c3b_readback_fails_on_a_leftover_fifo(vol):
    (vol / "analyzer_mirror").mkdir(parents=True)
    os.mkfifo(vol / "analyzer_mirror" / "pipe")
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is False and rb["leftover_kinds"] == {"other": 1}


def test_c3b_readback_fails_on_a_symlinked_scope_root_and_execute_records_completed_false(vol, tmp_path_factory,
                                                                                          capsys):
    outside = tmp_path_factory.mktemp("outside-root")
    os.symlink(outside, vol / "analyzer_generations")  # even pointing at an EMPTY dir: never trusted
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is False and rb["problems"] == ["SYMLINKED_SCOPE_ROOT:@data/analyzer_generations"]
    assert rb["leftover_entries"] == [{"rel": "@data/analyzer_generations", "kind": "symlink"}]
    rc = frna.run_cli(_good(vol), fetch=lambda url: dict(HEALTH), env=ENV, unlink=os.unlink)
    assert rc == 9
    assert "FLY_ANALYZER_READBACK_FAILED" in capsys.readouterr().out
    receipt = json.loads(next((vol / "runtime" / frna.RECEIPTS_DIRNAME).glob("*/receipt-complete.json")).read_text())
    assert receipt["completed"] is False
    assert os.path.islink(vol / "analyzer_generations") and outside.exists()


def test_c3b_readback_fails_on_a_non_directory_scope_root_and_a_dangling_pointer(vol, tmp_path):
    _w(vol / "analyzer_mirror", "not a dir")
    os.symlink(tmp_path / "nope.json", vol / "analyzer_current.json")
    rb = frna.fly_analyzer_readback(vol)
    assert rb["ok"] is False
    assert rb["problems"] == ["SCOPE_ROOT_NOT_A_DIRECTORY:@data/analyzer_mirror"]
    assert rb["pointer_present"] == ["analyzer_current.json"]


def test_c3b_classify_fly_scope_rejects_traversal_and_empty_segments():
    for rel in ("@data/analyzer_mirror/../runtime/state.json", "@data/analyzer_generations/../../etc/passwd",
                "@data/analyzer_mirror/a/../../segment-store/x.jsonl", "@data/../analyzer_current.json",
                "@data/analyzer_mirror//x.json", "@data/analyzer_mirror/./x.json", "@data/analyzer_mirror\\..\\x",
                "@data/.."):
        assert frna.classify_fly_scope(rel) == (frna.UNCLASSIFIED, "FLY_SCOPE_PATH_TRAVERSAL"), rel
        assert frna.classify_candidate(rel)[0] != frna.DELETE, rel
    assert frna.classify_fly_scope("@data/analyzer_mirror/x.json") == (frna.DELETE, "FLY_ANALYZER_MIRROR")
    assert frna.classify_fly_scope("@data/analyzer_current.json") == (frna.DELETE, "FLY_ANALYZER_CURRENT_POINTER")

"""Multi-day strategy-lab inputs: Tier A + frozen archive + mirror, epoch-bounded and de-duplicated."""
import hashlib
import json
import os

import numpy as np
import pandas as pd
import pytest

from strategy_lab import client as C
from strategy_lab import rankings as R
from strategy_lab import stream_studies as SS
from strategy_lab.engine import load_ai_calls_union, run_strategy_lab
from strategy_lab.export import stage_strategy_lab, write_export
from strategy_lab.tape import (HistorySources, coverage_summary, default_history_sources, load_bitfinex_tape,
                               winners_by_source)

DAY = 86400
T0 = 1790553600                      # 2026-09-28T00:00:00Z
EPOCH = T0 + 3600


def _tape_row(ts, mid=84000.0):
    return {"schema": "market_microstructure_1s_v1", "bucket_ts": int(ts), "valid_bbo": True,
            "bid": mid - 0.5, "ask": mid + 0.5, "buy_qty": 0.1, "sell_qty": 0.1}


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _write_tier_a(root, dataset, day, rows, *, ts_null=False, corrupt=False):
    folder = os.path.join(root, "tierA", dataset, "v1", f"date={day}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "part-0000.parquet")
    frame = pd.DataFrame({"ts": [None if ts_null else float(r.get("bucket_ts") or r.get("minute_ts")
                                                            or r.get("fill_ts") or 0)
                                 for r in rows],
                          "row": [json.dumps(r) for r in rows]})
    frame["ts"] = frame["ts"].astype("float64")
    frame.to_parquet(path, index=False)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    with open(os.path.join(folder, "part-0000.manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"sha256": "0" * 64 if corrupt else digest, "rows": len(rows), "dataset": dataset}, fh)
    return path


@pytest.fixture
def sources(tmp_path):
    """archive: [T0, EPOCH+2h) (pre-epoch rows too); Tier A: [EPOCH+1h, EPOCH+3h); mirror: [EPOCH+2.5h, EPOCH+4h)."""
    archive = tmp_path / "archive" / "tree"
    rows = [_tape_row(t, 84000.0) for t in range(T0, EPOCH + 7200)]
    _write_jsonl(str(archive / "market_microstructure_1s.jsonl.1"), rows[: len(rows) // 2])
    _write_jsonl(str(archive / "market_microstructure_1s.jsonl"), rows[len(rows) // 2:])
    tier_a = tmp_path / "compact"
    _write_tier_a(str(tier_a), "bitfinex_l1_tape_1s", "2026-09-28",
                  [_tape_row(t, 84001.0) for t in range(EPOCH + 3600, EPOCH + 3 * 3600)], ts_null=True)
    mirror = tmp_path / "mirror"
    _write_jsonl(str(mirror / "market_microstructure_1s.jsonl"),
                 [_tape_row(t, 84002.0) for t in range(EPOCH + 9000, EPOCH + 4 * 3600)])
    return {"archive": str(archive), "tier_a": str(tier_a), "mirror": str(mirror), "tmp": tmp_path}


# ------------------------------------------------------------------ Tier A client
def test_load_tier_a_absent_returns_empty_frame(tmp_path):
    out = C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path), since=EPOCH)
    assert out.empty and list(out.columns) == ["ts", "row"]


def test_load_tier_a_fills_null_ts_from_bucket_ts_and_dedupes(tmp_path):
    rows = [_tape_row(EPOCH + i) for i in range(10)]
    _write_tier_a(str(tmp_path), "bitfinex_l1_tape_1s", "2026-09-28", rows + rows[:3], ts_null=True)
    out = C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path), dedupe="ts")
    assert len(out) == 10 and out["ts"].tolist() == [float(EPOCH + i) for i in range(10)]
    since = C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path), since=EPOCH + 5, dedupe="ts")
    assert since["ts"].min() == EPOCH + 5
    parsed = C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path), dedupe="ts", parse=True)
    assert "bid" in parsed.columns and len(parsed) == 10


def test_load_tier_a_refuses_hash_mismatch_without_raising(tmp_path):
    _write_tier_a(str(tmp_path), "bitfinex_l1_tape_1s", "2026-09-28", [_tape_row(EPOCH)], corrupt=True)
    out = C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path))
    assert out.empty and len(out.attrs["refused_partitions"]) == 1
    with pytest.raises(C.StaleExportError):
        C.load_tier_a("bitfinex_l1_tape_1s", root=str(tmp_path), strict=True)


# ------------------------------------------------------------------ tape union
def test_tape_union_is_epoch_bounded_deduped_and_mirror_wins(sources):
    hist = HistorySources(archive_dirs=(sources["archive"],), tier_a_root=sources["tier_a"])
    tape = load_bitfinex_tape(sources["mirror"], start_ts=EPOCH, cache_dir=str(sources["tmp"] / "cache"),
                              history=hist)
    assert tape.t0 == EPOCH                                   # archive rows before the epoch are excluded
    assert tape.t1 == EPOCH + 4 * 3600 - 1
    assert tape.present.all()                                 # union has no holes
    # archive [EPOCH, +2h) = 7200, Tier A 7200 rows, mirror [EPOCH+2.5h, +4h) = 5400 rows
    assert tape.source_rows == {"archive": 7200, "tier_a": 7200, "mirror": 5400}
    # winners: archive [0,1h), Tier A [1h,2.5h), mirror [2.5h,4h)
    assert tape.source_seconds == {"archive": 3600, "tier_a": 5400, "mirror": 5400}
    i = EPOCH + 9500 - tape.t0
    assert tape.bid[i] == 84002.0 - 0.5                       # mirror beats Tier A on the overlap
    assert tape.bid[EPOCH + 4000 - tape.t0] == 84001.0 - 0.5  # Tier A beats the archive
    cov = tape.coverage(epoch_start=EPOCH, now=EPOCH + 5 * 3600)
    assert cov["epoch_coverage_share"] == pytest.approx(4 / 5, abs=1e-4)   # lag to now counts as missing
    assert cov["in_window_present_share"] == 1.0 and cov["horizon_hours"] == pytest.approx(4.0)
    assert cov["sources"] == {"tier_a_rows": 7200, "archive_rows": 7200, "mirror_rows": 5400}
    assert cov["present_share"] == 1.0 and cov["start_ts"] == EPOCH          # legacy fields kept
    # second load is served from the per-file cache (archive files and Tier A partitions)
    again = load_bitfinex_tape(sources["mirror"], start_ts=EPOCH, cache_dir=str(sources["tmp"] / "cache"),
                               history=hist)
    assert again.cache_hits >= 3 and again.n == tape.n


def test_tape_without_history_matches_the_mirror_only_window(sources):
    tape = load_bitfinex_tape(sources["mirror"], start_ts=EPOCH)
    cov = tape.coverage(epoch_start=EPOCH, now=EPOCH + 4 * 3600)
    assert cov["in_window_present_share"] == 1.0                  # looks healthy inside the window ...
    assert cov["epoch_coverage_share"] == pytest.approx(5400 / (4 * 3600), abs=1e-4)   # ... but not epoch-wide
    assert cov["sources"]["archive_rows"] == 0 and cov["sources"]["mirror_rows"] == 5400


def test_corrupt_tier_a_partition_degrades_to_other_sources(sources):
    _write_tier_a(sources["tier_a"], "bitfinex_l1_tape_1s", "2026-09-28",
                  [_tape_row(t, 84001.0) for t in range(EPOCH + 3600, EPOCH + 3 * 3600)], corrupt=True)
    hist = HistorySources(archive_dirs=(sources["archive"],), tier_a_root=sources["tier_a"])
    tape = load_bitfinex_tape(sources["mirror"], start_ts=EPOCH, history=hist)
    assert tape.source_rows["tier_a"] == 0 and tape.history["refused_tier_a_partitions"]
    assert not tape.present.all()                                 # the Tier A-only seconds are a hole


def test_coverage_math_and_winner_attribution():
    cov = coverage_summary(epoch_start=0, first_ts=100, last_ts=199, present_units=50, window_units=100,
                           unit_sec=1, now=1000)
    assert cov["epoch_coverage_share"] == 0.05 and cov["in_window_present_share"] == 0.5
    assert cov["horizon_hours"] == pytest.approx(100 / 3600, abs=1e-3)
    minutes = coverage_summary(epoch_start=0, first_ts=0, last_ts=540, present_units=10, window_units=10,
                               unit_sec=60, now=600)
    assert minutes["epoch_coverage_share"] == 1.0
    w = winners_by_source(np.array([1, 1, 2, 3, 3, 3]), np.array([0, 2, 1, 0, 1, 2]))
    assert w == {"archive": 0, "tier_a": 1, "mirror": 2}


def test_default_history_sources_are_off_under_pytest(monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_ARCHIVE_DIRS", raising=False)
    monkeypatch.delenv("STRATEGY_LAB_TIER_A_ROOT", raising=False)
    monkeypatch.delenv("DOXXED_BOT_DATA_COMPACT_DIR", raising=False)
    assert not default_history_sources().enabled


def test_ai_calls_union_dedupes_by_call_id_and_fills_funding_from_archive(tmp_path):
    def calls(folder, ids, start):
        os.makedirs(folder, exist_ok=True)
        pd.DataFrame([{"ts": pd.Timestamp(start + 300 * k, unit="s", tz="UTC").isoformat(), "event": "AI_DECISION",
                       "long_score": 70, "short_score": 30, "shared_ai_call_id": cid, "trade_id": cid}
                      for k, cid in enumerate(ids)]).to_csv(os.path.join(folder, "ai_tranche_log.csv"), index=False)
    arch, mirror = str(tmp_path / "arch"), str(tmp_path / "mirror")
    calls(arch, ["pre", "a1", "a2", "m1"], EPOCH - 300)
    calls(mirror, ["m1", "m2"], EPOCH + 600)
    _write_jsonl(os.path.join(arch, "ai_input_log.jsonl"),
                 [{"trade_id": "a1", "context": {"funding": {"rate": 0.0001}}}])
    out, rows, unique = load_ai_calls_union(mirror, EPOCH, HistorySources(archive_dirs=(arch,)))
    assert out["call_id"].tolist() == ["a1", "a2", "m1", "m2"]      # "pre" is before the epoch
    assert rows == {"archive": 3, "tier_a": 0, "mirror": 2} and unique == {"archive": 2, "tier_a": 0, "mirror": 2}
    assert out.set_index("call_id").loc["a1", "funding_bp_8h"] == pytest.approx(1.0)


def test_engine_reports_stream_coverage_and_export_carries_it(sources, tmp_path):
    hist = HistorySources(archive_dirs=(sources["archive"],), tier_a_root=sources["tier_a"])
    session = {"collector_v22_epoch_ts": EPOCH, "collector_v22_epoch_id": "epoch-test"}
    now = EPOCH + 4 * 3600
    payload, tables = run_strategy_lab(sources["mirror"], session=session, cache_dir=str(tmp_path / "c"),
                                       history=hist, now=now)
    assert payload["status"] == "OK"
    tape_cov = payload["stream_coverage"]["market_microstructure_1s.jsonl"]
    assert tape_cov["epoch_coverage_share"] == pytest.approx(1.0, abs=1e-3)
    assert tape_cov["sources"]["archive_rows"] == 7200 and payload["tape"]["present_share"] == 1.0
    assert payload["history_sources"]["archive_dirs"] == [sources["archive"]]
    base, _ = run_strategy_lab(sources["mirror"], session=session, history=HistorySources(), now=now)
    assert base["stream_coverage"]["market_microstructure_1s.jsonl"]["epoch_coverage_share"] < 0.5
    stage_strategy_lab(payload, tables)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report_manifest.json").write_text(json.dumps({"generation_id": "g1"}), encoding="utf-8")
    (report_dir / "event_study_report.json").write_text(json.dumps(
        {"schema": "event_study_report_v1", "hypotheses": [{"id": "H1", "spec_hash": "abc", "status": "LOCKBOX_ACCRUING",
                                                            "lockbox": {"events_counted": 7}}]}), encoding="utf-8")
    (report_dir / "data_health_report.json").write_text(json.dumps(
        {"status": "OK", "streams": [{"stream": "bfx_1s", "status": "OK", "coverage_pct_24h": 99.0}]}),
        encoding="utf-8")
    summary = write_export(report_dir=str(report_dir), data_dir=sources["mirror"], trades=None, registry={},
                           lanes=[], root=str(tmp_path / "exports"), now=now)
    assert summary["stream_coverage"]["market_microstructure_1s.jsonl"]["epoch_coverage_share"] is not None
    assert summary["strategy_lab"]["stream_coverage"] == summary["stream_coverage"]
    assert summary["event_study"]["hypotheses"][0]["lockbox_events_counted"] == 7
    assert summary["data_health"]["streams"]["bfx_1s"]["status"] == "OK"
    health = pd.read_csv(tmp_path / "exports" / "latest" / "stream_health.csv").set_index("stream")
    assert health.loc["market_microstructure_1s.jsonl", "archive_rows"] == 7200
    assert summary["tables"]["event_study_hypotheses"]["rows"] == 1


# ------------------------------------------------------------------ stream studies
def test_stream_studies_add_archive_files_and_tier_a_rows_without_double_counting(tmp_path):
    def markout(tid, ts):
        return {"schema": "fill_markout_v1", "fill_ts": ts, "trade_id": tid, "research_lane": "FAMILY_A",
                "liquidity": "MAKER", "markouts": {"1s": {"markout_mid_bps": 1.0}}}
    arch, mirror, compact = tmp_path / "arch", tmp_path / "mirror", tmp_path / "compact"
    _write_jsonl(str(arch / "fill_markouts.jsonl"), [markout("t0", EPOCH - 10), markout("t1", EPOCH + 10),
                                                     markout("t2", EPOCH + 20)])
    _write_jsonl(str(mirror / "fill_markouts.jsonl"), [markout("t2", EPOCH + 20), markout("t3", EPOCH + 30)])
    _write_tier_a(str(compact), "fill_markouts", "2026-09-28", [markout("t3", EPOCH + 30), markout("t4", EPOCH + 40)])
    kw = dict(trades=pd.DataFrame(), registry={}, lanes=["FAMILY_A"], epoch_id="ep", epoch_start=float(EPOCH),
              cache_dir=str(tmp_path / "cache"), now=EPOCH + 100)
    payload, tables = SS.run_stream_studies(str(mirror), history=HistorySources(archive_dirs=(str(arch),),
                                                                                tier_a_root=str(compact)), **kw)
    assert payload["fill_markouts"]["trades"] == 4                # t1..t4; t0 is pre-epoch; overlaps deduped
    health = tables["stream_study_health"].set_index("stream")
    assert health.loc["fill_markouts.jsonl", "archive_files"] == 1
    assert health.loc["fill_markouts.jsonl", "tier_a_rows"] == 2
    mirror_only, _ = SS.run_stream_studies(str(mirror), history=HistorySources(), **kw)
    assert mirror_only["fill_markouts"]["trades"] == 2


# ------------------------------------------------------------------ pooled registry tiles
def _rollup(root, day, epochs, final=True):
    folder = os.path.join(root, "rollups", "daily" if final else "open")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{day}.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": "analysis_archive_rollup", "schema_version": 1, "period": "day", "day": day,
                   "final": final, "epochs": epochs}, fh)


def _stats(values):
    v = np.asarray(values, float)
    return {"n": len(v), "wins": int((v > 0).sum()), "losses": int((v < 0).sum()), "sum": float(v.sum()),
            "sumsq": float((v * v).sum()), "min": float(v.min()), "max": float(v.max())}


def test_pooled_tiles_combine_raw_and_archive_for_registry_lanes_only(tmp_path):
    root = str(tmp_path / "archive")
    epoch = "epoch-cur"
    _rollup(root, "2026-09-28", {
        epoch: {"by_tile": {"FAMILY_A": _stats([0.1] * 12), "FAMILY_RETIRED": _stats([5.0] * 50),
                            "NON_REGISTRY_LANE": _stats([9.0] * 40)}},
        "epoch-old": {"by_tile": {"FAMILY_A": _stats([-3.0] * 30)}}})
    # same day as raw trades: the raw cell (more closes) must win, never both
    _rollup(root, "2026-09-29", {epoch: {"by_tile": {"FAMILY_A": _stats([0.2] * 2)}}})
    raw_day = pd.Timestamp("2026-09-29T12:00:00Z")
    trades = pd.DataFrame([{"trade_id": f"r{i}", "research_lane": "FAMILY_A", "net_pnl_usd": 0.3,
                            "close_ts": (raw_day + pd.Timedelta(minutes=i)).isoformat(), "epoch_id": epoch,
                            "exit_reason": "TIME"} for i in range(5)])
    payload, _ = R.build_main_rankings(trades, ["FAMILY_A"], reports={}, epoch_id=epoch, archive_root=root)
    pool = payload["tile_pool"]
    rows = {r["key"]: r for r in pool["rows"]}
    a = rows["FAMILY_A"]
    assert (a["n"], a["n_raw"], a["n_archive"]) == (17, 5, 12)
    assert a["mean_usd"] == pytest.approx((12 * 0.1 + 5 * 0.3) / 17, abs=1e-6)
    assert pool["epoch_id"] == epoch
    assert set(rows) == {"FAMILY_A"}                              # unregistered archive lanes never pool
    assert "NON_REGISTRY_LANE" in pool["excluded_archive_lanes"]
    assert payload["families"]["tiles"]["rows"][0]["n"] == 5      # the raw-only family is unchanged
    assert "tiles_pooled" in payload["family_summaries"] and "registry tiles" in payload["method"]["tiles_pooled"]


def test_pooled_tiles_never_include_retired_lanes_even_if_requested(tmp_path, monkeypatch):
    root = str(tmp_path / "archive")
    _rollup(root, "2026-09-28", {"ep": {"by_tile": {"FAMILY_RETIRED": _stats([5.0] * 50)}}})
    monkeypatch.setattr(R, "_retired_lanes", lambda: frozenset({"FAMILY_RETIRED"}))
    payload, _ = R.build_main_rankings(pd.DataFrame(), ["FAMILY_RETIRED", "FAMILY_A"], reports={}, epoch_id="ep",
                                       archive_root=root)
    keys = [r["key"] for r in payload["tile_pool"]["rows"]]
    assert keys == ["FAMILY_A"]


def test_rankings_without_archive_report_not_used():
    payload, _ = R.build_main_rankings(pd.DataFrame(), ["FAMILY_A"], reports={})
    assert payload["tile_pool"]["status"] == "NOT_USED" and "tiles_pooled" not in payload["families"]

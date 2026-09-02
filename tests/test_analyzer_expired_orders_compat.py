import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
ANALYZER = AGENT / "analyzer_research_engine_v62.py"


def _load_analyzer():
    sys.path.insert(0, str(AGENT))
    spec = importlib.util.spec_from_file_location("expired_orders_compat_analyzer", ANALYZER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_record_separator_is_recovered_without_changing_source(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "expired.csv"
    path.write_bytes(
        b"time,trade_id,dir,evidence\r"
        b"2026-09-01T00:00:00+00:00,first,LONG,\"partial"
        b"2026-09-01T00:01:00+00:00,second,SHORT,complete\"\r"
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    frame = analyzer._load_expired_orders_csv(path)

    assert list(frame["trade_id"]) == ["first", "second"]
    assert set(frame["_csv_parse_status"]) == {"RECOVERED_MISSING_RECORD_SEPARATOR"}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_absent_trailing_schema_fields_are_explicit_unknown(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "expired.csv"
    path.write_text(
        "time,trade_id,dir,reason,research_lane\r"
        "2026-09-01T00:00:00+00:00,first,LONG\r",
        encoding="utf-8",
        newline="",
    )

    frame = analyzer._load_expired_orders_csv(path)

    assert frame.iloc[0]["reason"] == "UNKNOWN"
    assert frame.iloc[0]["research_lane"] == "UNKNOWN"
    assert frame.iloc[0]["_csv_missing_fields"] == 2
    assert frame.iloc[0]["_csv_parse_status"] == "NORMALIZED_MISSING_FIELDS"


def test_unprovable_overwide_row_fails_closed(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "expired.csv"
    path.write_text(
        "time,trade_id,dir\r"
        "2026-09-01T00:00:00+00:00,first,LONG,unexpected\r",
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(ValueError, match="EXPIRED_ORDERS_SCHEMA_OVERFLOW"):
        analyzer._load_expired_orders_csv(path)


def test_current_canonical_damage_yields_both_joined_trade_ids():
    analyzer = _load_analyzer()
    path = AGENT / "canonical-research-data" / "expired_orders_3factor.csv"

    frame = analyzer._load_expired_orders_csv(path)

    assert len(frame) == 828
    recovered = frame[frame["_csv_parse_status"] == "RECOVERED_MISSING_RECORD_SEPARATOR"]
    assert list(recovered["trade_id"]) == ["fmg-5fc04ee8e407", "fc3-d216195d52be"]

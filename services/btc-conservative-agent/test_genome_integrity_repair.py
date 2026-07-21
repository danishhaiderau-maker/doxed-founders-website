"""Focused contracts for the Trading Genome integrity repair."""
import copy
import json
import os
import sqlite3
import tempfile

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

from research.genome.clustering import build_cluster_library
from research.genome.discoveries import generate_discoveries
from research.genome.fingerprints import market_fingerprint
from research.genome.library_store import GenomeLibraryStore
from research.genome.memory import merge_cluster_into_library
from research.genome.quality_score import research_confidence_label
from research.genome.similarity import nearest_cluster
from research.genome.validation import validate_genome_integrity
from research_genome.store import ResearchStore
from verify_genome_rebuild_idempotency import _derived_signature, _verify_staging


def run():
    passed = 0

    def check(name, condition):
        nonlocal passed
        if not condition:
            raise AssertionError(name)
        passed += 1

    missing = market_fingerprint({"market_genome_id": "m0"})
    check("missing ADX is UNKNOWN", missing["adx"] is None and missing["adx_bucket"] == "UNKNOWN_ADX")
    check("missing spread is UNKNOWN", missing["spread"] is None and missing["spread_bucket"] == "UNKNOWN_SPREAD")

    rows = []
    for index in range(15):
        row = {
            "market_genome_id": f"m{index}",
            "ts": f"2026-07-21T00:{index:02d}:00+00:00",
            "trading_session": "ASIA",
            "adx": 25.0,
            "spread": 3,
        }
        if index == 14:
            row.update({
                "atr": 110.0,
                "volatility_percentile": 60.0,
                "volume_percentile": 55.0,
                "bull_score": 7.0,
                "bear_score": 4.0,
                "momentum": 0.4,
                "structure": 5.0,
            })
        rows.append(row)
    clusters = build_cluster_library(rows, k=8)
    check("cluster created", len(clusters) == 1)
    cluster = clusters[0]
    check("finite-only centroid keeps missing sparse dimensions nonzero", cluster["centroid"]["atr"] == 110.0)
    check("centroid coverage is explicit", cluster["centroid_coverage"]["atr"] == {"present": 1, "total": 15, "ratio": 0.0667})
    check("representative prefers feature completeness", cluster["representative"]["atr"] == 110.0)

    insufficient = nearest_cluster(
        {"adx": 26.0},
        [{"genome_id": "G1", "is_validated_cluster": True, "centroid": {"adx": 25.0}}],
    )
    check("similarity fails closed with too few shared features", insufficient["cluster_id"] == "UNKNOWN")
    enough = nearest_cluster(
        {"adx": 26.0, "atr": 100.0, "spread": 3.0},
        [{
            "genome_id": "G1",
            "is_validated_cluster": True,
            "centroid": {"adx": 25.0, "atr": 100.0, "spread": 3.0},
        }],
    )
    check("normalized populated similarity matches", enough["cluster_id"] == "G1")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "memory.db")
        memory_store = GenomeLibraryStore(db_path)
        first = merge_cluster_into_library(memory_store, clusters, memory_store.load_all_genomes())
        second = merge_cluster_into_library(memory_store, clusters, memory_store.load_all_genomes())
        check("full rescan does not inflate observations", first[0]["observations"] == second[0]["observations"] == 15)
        check("same watermark writes one genome evidence row", len(memory_store.load_ledger("genome", first[0]["genome_id"])) == 1)

        discovery_rows = [
            {
                "trade_id": f"t{index}", "pnl_usd": 1.0, "is_weekend": False,
                "session": "ASIA", "adx_bucket": "MID_ADX", "spread_bucket": "SPREAD3",
                "direction": "LONG", "regime": "TREND",
            }
            for index in range(10)
        ]
        first_discoveries = generate_discoveries(discovery_rows, memory_store)
        second_discoveries = generate_discoveries(discovery_rows, memory_store)
        discovery_id = first_discoveries[0]["discovery_id"]
        check("discovery id is deterministic", discovery_id == second_discoveries[0]["discovery_id"])
        check("same discovery evidence is idempotent", len(memory_store.load_ledger("discovery", discovery_id)) == 1)
        other_rows = [dict(row, trade_id=f"s{index}", direction="SHORT") for index, row in enumerate(discovery_rows)]
        other = generate_discoveries(other_rows, memory_store)[0]
        check("distinct DNA keys cannot collide", other["discovery_id"] != discovery_id)

    check("confidence 29 is LOW", research_confidence_label(29) == "LOW")
    check("confidence 30 is MODERATE", research_confidence_label(30) == "MODERATE")
    check("confidence 199 remains MODERATE", research_confidence_label(199) == "MODERATE")
    check("confidence 200 is HIGH", research_confidence_label(200) == "HIGH")

    with tempfile.TemporaryDirectory() as tmp:
        source_store = ResearchStore(tmp)
        market = {
            "market_genome_id": "m1", "environment_id": "e1", "adx": 30, "atr": 100,
            "spread": 3, "structure": 5, "momentum": 0.5, "volatility_percentile": 60,
            "volume_percentile": 55, "long_score": 70, "short_score": 40,
        }
        source_store.upsert_layer("market", "m1", market, "environment_id", "e1")
        source_store.upsert_layer(
            "decision", "d1", {"decision_id": "d1", "market_genome_id": "m1", "trade_id": "source-1"},
            "market_genome_id", "m1",
        )
        source_store.upsert_layer(
            "trade", "closed-1",
            {"trade_id": "closed-1", "source_trade_id": "source-1", "decision_id": "d1"},
            "decision_id", "d1",
        )
        quality = validate_genome_integrity(source_store.db_path)
        check("complete linked sample passes integrity", quality["verdict"] == "PASS")
        check("trade lineage linkage is measured", quality["data_quality"]["trade_decision_linkage"]["ratio"] == 1.0)
        source_store._conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        broken_store = ResearchStore(tmp)
        broken_store.upsert_layer(
            "decision", "orphan-d1",
            {"decision_id": "orphan-d1", "market_genome_id": "missing-market", "trade_id": "source-1"},
            "market_genome_id", "missing-market",
        )
        broken_quality = validate_genome_integrity(broken_store.db_path)
        check("structural orphan fails integrity", broken_quality["verdict"] == "FAIL")
        broken_store._conn.close()
        GenomeLibraryStore(broken_store.db_path)
        try:
            _verify_staging(broken_store.db_path, os.path.join(tmp, "artifacts"))
        except AssertionError:
            blocked = True
        else:
            blocked = False
        check("structural orphan blocks staged publication", blocked)

    with tempfile.TemporaryDirectory() as tmp:
        duplicate_store = ResearchStore(tmp)
        now = "2026-07-21T00:00:00+00:00"
        for key in ("row-1", "row-2"):
            duplicate_store._conn.execute(
                "INSERT INTO trade_genome(trade_id, decision_id, ts, payload_json) VALUES(?,?,?,?)",
                (key, "", now, json.dumps({"trade_id": "logical-duplicate"})),
            )
        duplicate_store._conn.commit()
        duplicate_quality = validate_genome_integrity(duplicate_store.db_path)
        check("duplicate logical trade IDs fail integrity", duplicate_quality["verdict"] == "FAIL")
        duplicate_store._conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        sparse_store = ResearchStore(tmp)
        sparse_store.upsert_layer(
            "market", "m-sparse", {"market_genome_id": "m-sparse", "environment_id": "e-sparse"},
            "environment_id", "e-sparse",
        )
        sparse_quality = validate_genome_integrity(sparse_store.db_path)
        check("coverage-only deficits remain warnings", sparse_quality["verdict"] == "WARN")
        sparse_store._conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        signature_db = os.path.join(tmp, "derived.db")
        conn = sqlite3.connect(signature_db)
        conn.executescript("""
            CREATE TABLE genome_library(
              genome_id TEXT PRIMARY KEY, fingerprint_key TEXT NOT NULL,
              first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              observations INTEGER NOT NULL, trade_count INTEGER NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE genome_discovery_memory(
              discovery_id TEXT PRIMARY KEY, dna_key TEXT NOT NULL,
              first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              status TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE genome_evidence_ledger(
              id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL,
              entity_id TEXT NOT NULL, period_key TEXT NOT NULL,
              ts TEXT NOT NULL, payload_json TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO genome_library VALUES(?,?,?,?,?,?,?)",
            ("G1", "K", "a", "b", 5, 1, json.dumps({"ev": 1.0, "ts": "volatile-a"})),
        )
        conn.execute(
            "INSERT INTO genome_discovery_memory VALUES(?,?,?,?,?,?)",
            ("D1", "K", "a", "b", "NEW", json.dumps({"ev": 1.0})),
        )
        conn.execute(
            "INSERT INTO genome_evidence_ledger VALUES(?,?,?,?,?,?)",
            (1, "genome", "G1", "2026-W30", "a", json.dumps({"ev": 1.0})),
        )
        conn.commit()
        before = _derived_signature(signature_db)
        conn.execute(
            "UPDATE genome_library SET payload_json=? WHERE genome_id='G1'",
            (json.dumps({"ev": 2.0, "ts": "volatile-b"}),),
        )
        conn.commit()
        after = _derived_signature(signature_db)
        check("semantic signature catches content drift at equal counts", before != after)
        conn.close()

    import bot

    class FakeBridge:
        def __init__(self):
            self.scans = []
            self.decisions = []

        def on_ai_scan_complete(self, market):
            self.scans.append(market)
            return {"environment_id": "env-1", "market_genome_id": "market-1"}

        def on_ai_decision(self, approved, **payload):
            self.decisions.append({"approved": approved, **payload})
            return "decision-1"

    fake = FakeBridge()
    original_get_bridge = bot.get_genome_bridge
    with bot.state_lock:
        saved = copy.deepcopy(bot.state)
        bot.state["price"] = 65000
        bot.state["regime"] = "TREND"
        bot.state["ema_status"] = {"ema_fast": 1, "ema_slow": 2, "ema_spread": -1}
        bot.state["market_context"] = {
            "trend_strength": {"adx": 31},
            "market_structure": {"structure_score": 6},
        }
        bot.state["feature_snapshot"] = {
            "volatility_atr": 125,
            "volatility_percentile": 67,
            "volume_percentile": 42,
            "ret_1m": 0.0001,
            "ret_5m": 0.0002,
            "velocity": 0.0001,
        }
    try:
        bot.get_genome_bridge = lambda: fake
        ai = {
            "decision": "APPROVE", "direction": "LONG", "trade_id": "source-ai-1",
            "long_score": 70, "short_score": 50, "bull_score": 7, "bear_score": 5,
        }
        bot._emit_genome_ai_events(ai)
        bot._emit_genome_ai_events(ai)
        scan = fake.scans[0]
        check("one shared AI result emits one scan", len(fake.scans) == 1)
        check("source scores captured", scan["long_score"] == 70 and scan["short_score"] == 50)
        check("directional spread captured", scan["directional_spread"] == 2)
        check("feature and structure sources captured", scan["volatility_percentile"] == 67 and scan["structure"] == 6)
        check("AI lineage is persisted on result", ai["genome_market_id"] == "market-1" and ai["genome_decision_id"] == "decision-1")
        position = bot._build_open_position(
            {"trade_id": "source-ai-1", "limit_price": 65000, "qty": 0.01},
            {"trade_id": "source-ai-1", "final_direction": "LONG", "signal_price": 65000},
            ai,
        )
        check("position inherits source lineage", position["genome_market_id"] == "market-1")
    finally:
        bot.get_genome_bridge = original_get_bridge
        with bot.state_lock:
            bot.state.clear()
            bot.state.update(saved)

    print(f"PASS: {passed} Trading Genome integrity checks")


if __name__ == "__main__":
    run()

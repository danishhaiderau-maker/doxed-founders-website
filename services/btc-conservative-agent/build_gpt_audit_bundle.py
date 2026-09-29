#!/usr/bin/env python3
"""
GPT Architecture Audit Bundle — one-click source review export.

Includes the production bot/analyzer source closure, genome/recorder modules,
available provenance reports, and an implementation checklist.

This is deliberately not the complete research-evidence download. Raw market,
order, fill, lifecycle, trade, shadow and full analyzer-report artifacts belong
to the separately verified "Download Everything" export.

Output: research/downloads/gpt_audit_bundle.zip
        research/downloads/GPT_AUDIT_MANIFEST.json

Regenerated automatically after each genome analyzer run (~30 min).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

AGENT_ROOT = Path(__file__).resolve().parent
RESEARCH_ROOT = AGENT_ROOT / "research"
OUT_DIR = RESEARCH_ROOT / "downloads"
ZIP_NAME = "gpt_audit_bundle.zip"
MANIFEST_NAME = "GPT_AUDIT_MANIFEST.json"
ARCHITECTURE_VERSION = "v11.0-genome-architecture-v1"

REQUIRED_SOURCE_FILES = (
    "bot.py",
    "analyzer_research_engine_v62.py",
    "btc_conservative_agent.py",
    "combo_pathway_config.py",
    "pathway_lane_roster.py",
    "policy_research_engine.py",
    "research_v3_bridge.py",
    "research_v3_candidates.py",
    "research_v3_contract.py",
    "research/analysis_eligibility.py",
    "research/counterfactual_normalization.py",
    "research/platform_relay_evidence.py",
    "research/research_dashboard.py",
    "research/genome/run_analyzer.py",
    "research/shadow_outcome_reconstruction.py",
    "research/source_market_evidence.py",
)

# Production Python is intentionally allowlisted by location and excludes tests.
# This captures modules reached through dynamic loaders as well as normal imports.
SOURCE_TREES = ("research", "research_genome")

DATA_FILES = (
    "research/genome/genome_analysis_report.json",
    "research/genome/genome_library.json",
    "research/genome/genome_discoveries.json",
    "bot_analyzer_sync.json",
    "report_manifest.json",
    "research_session.json",
    "pathway_lane_specs.json",
    "research/schema/genome_v1.json",
)

DOC_FILES = (
    ("../../docs/research-genome-schema-v1.md", "docs/research-genome-schema-v1.md"),
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _production_source_files() -> List[Path]:
    """Return the deterministic production source closure for the audit."""
    candidates = {AGENT_ROOT / rel for rel in REQUIRED_SOURCE_FILES}
    candidates.update(
        path for path in AGENT_ROOT.glob("*.py")
        if path.is_file() and not path.name.startswith("test_")
    )
    for tree in SOURCE_TREES:
        root = AGENT_ROOT / tree
        if not root.is_dir():
            continue
        candidates.update(
            path for path in root.rglob("*.py")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not path.name.startswith("test_")
        )
    return sorted(candidates, key=lambda path: path.relative_to(AGENT_ROOT).as_posix())


def _validate_required_and_promised_members(
    files: List[Tuple[Path, str]], manifest: Dict[str, Any]
) -> None:
    members = {arc for _src, arc in files}
    required = set(manifest.get("required_members") or [])
    promised = set(manifest.get("start_here") or [])
    missing_required = sorted(required - members)
    missing_promised = sorted(promised - members)
    if missing_required or missing_promised:
        details = []
        if missing_required:
            details.append(f"required={missing_required}")
        if missing_promised:
            details.append(f"start_here={missing_promised}")
        raise FileNotFoundError("audit bundle incomplete: " + "; ".join(details))


def _db_stats(db_path: Path) -> Dict[str, Any]:
    if not db_path.is_file():
        return {"exists": False}
    try:
        conn = sqlite3.connect(str(db_path))
        tables = {}
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            name = row[0]
            try:
                tables[name] = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            except sqlite3.Error:
                tables[name] = "error"
        conn.close()
        return {"exists": True, "tables": tables}
    except sqlite3.Error as exc:
        return {"exists": True, "error": str(exc)}


def _implementation_checklist() -> Dict[str, Any]:
    genome_dir = RESEARCH_ROOT / "genome"
    modules = sorted(p.name for p in genome_dir.glob("*.py") if p.is_file())
    return {
        "architecture_version": ARCHITECTURE_VERSION,
        "schema_version": "1.0.0",
        "analyzer_mode": "DNA_FIRST_MEMORY",
        "execution_automation": "NEVER — human review only",
        "components": {
            "execution_engine_scenario_c": {"status": "IMPLEMENTED", "note": "bot.py frozen tiles"},
            "genome_recorders": {"status": "IMPLEMENTED", "path": "research_genome/"},
            "persistent_genome_library": {"status": "IMPLEMENTED", "module": "library_store.py"},
            "genome_memory": {"status": "IMPLEMENTED", "module": "memory.py"},
            "genome_validation": {"status": "IMPLEMENTED", "module": "validation.py"},
            "discovery_engine": {"status": "IMPLEMENTED", "module": "discoveries.py"},
            "statistical_evidence": {"status": "IMPLEMENTED", "module": "evidence.py"},
            "evidence_ledger": {"status": "IMPLEMENTED", "table": "genome_evidence_ledger"},
            "unknown_market_safeguard": {"status": "IMPLEMENTED", "module": "run_analyzer.py"},
            "recommendation_gates": {"status": "IMPLEMENTED", "gate": "recommendation_allowed n>=30"},
            "genome_identity_labels": {"status": "IMPLEMENTED", "module": "identity.py"},
            "genome_vs_cluster_taxonomy": {"status": "IMPLEMENTED", "module": "taxonomy.py"},
            "replay_capabilities_doc": {"status": "IMPLEMENTED", "module": "run_analyzer.py replay_capabilities"},
            "decision_genome_full": {"status": "PARTIAL", "note": "counts only; Priority 4"},
            "outcome_genome_full": {"status": "PARTIAL", "note": "fingerprints active"},
            "replay_audit": {"status": "NOT_STARTED", "note": "Priority 5 — per-genome UI; lifecycle replay ACTIVE"},
            "evolution_timeline_viz": {"status": "PARTIAL", "note": "ledger supports; UI pending"},
            "v62_retirement": {"status": "NOT_STARTED", "note": "parallel until parity"},
        },
        "genome_modules": modules,
        "data_collection_phase": {
            "target_500_trades": "Validate recorder + genome integrity",
            "target_1000_trades": "Evaluate discovery quality",
            "target_2000_3000_trades": "Out-of-sample genome validation",
        },
    }


def _atomic_zip_build(files: List[Tuple[Path, str]], out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(out_zip.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for src, arc in files:
                zf.write(src, arcname=arc)
        with zipfile.ZipFile(tmp_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise zipfile.BadZipFile(f"corrupt member: {bad}")
        shutil.move(str(tmp_path), str(out_zip))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def build(agent_root: Path | None = None) -> Tuple[Path, dict]:
    global AGENT_ROOT, RESEARCH_ROOT, OUT_DIR
    if agent_root is not None:
        AGENT_ROOT = Path(agent_root).resolve()
        RESEARCH_ROOT = AGENT_ROOT / "research"
        OUT_DIR = RESEARCH_ROOT / "downloads"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    to_pack: List[Tuple[Path, str]] = []
    file_index: List[Dict[str, Any]] = []

    missing_source = [
        rel for rel in REQUIRED_SOURCE_FILES if not (AGENT_ROOT / rel).is_file()
    ]
    if missing_source:
        raise FileNotFoundError(
            "required production audit source missing: " + ", ".join(missing_source)
        )

    for src in _production_source_files():
        rel = src.relative_to(AGENT_ROOT).as_posix()
        arc = f"source/{rel}"
        to_pack.append((src, arc))
        file_index.append({
            "path": arc,
            "bytes": src.stat().st_size,
            "sha256_prefix": _file_sha256(src),
        })

    for rel in DATA_FILES:
        src = AGENT_ROOT / rel
        if src.is_file():
            arc = f"data/{Path(rel).name}"
            to_pack.append((src, arc))
            file_index.append({
                "path": arc,
                "bytes": src.stat().st_size,
                "sha256_prefix": _file_sha256(src),
            })

    repo_root = AGENT_ROOT.parent.parent
    for src_rel, arc in DOC_FILES:
        src = (AGENT_ROOT / src_rel).resolve()
        if not src.is_file():
            src = repo_root / "docs" / Path(arc).name
        if src.is_file():
            to_pack.append((src, arc))
            file_index.append({
                "path": arc,
                "bytes": src.stat().st_size,
                "sha256_prefix": _file_sha256(src),
            })

    report_path = RESEARCH_ROOT / "genome" / "genome_analysis_report.json"
    report_summary: Dict[str, Any] = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_summary = {
                "generated_at": report.get("generated_at"),
                "layer_counts": report.get("layer_counts"),
                "genome_memory": report.get("genome_memory"),
                "discoveries_count": len(report.get("discoveries") or []),
                "recommendation": report.get("recommendation", {}).get("action"),
                "validation_verdict": (report.get("validation") or {}).get("verdict"),
            }
        except (json.JSONDecodeError, OSError):
            pass

    start_here = [
        "GPT_AUDIT_MANIFEST.json",
        "README.txt",
        "IMPLEMENTATION_STATUS.json",
        "source/bot.py",
        "source/analyzer_research_engine_v62.py",
        "source/research/research_dashboard.py",
        "source/research/genome/run_analyzer.py",
    ]
    if report_path.is_file():
        start_here.append("data/genome_analysis_report.json")

    manifest: Dict[str, Any] = {
        "schema": "gpt_audit_bundle_manifest_v1",
        "generated_at": stamp,
        "architecture_version": ARCHITECTURE_VERSION,
        "bundle_type": "ARCHITECTURE_SOURCE_AUDIT",
        "purpose": "Source and architecture review of the trading and genome implementation",
        "evidence_scope": "SOURCE_AND_OPTIONAL_DERIVED_GENOME_SUMMARY_ONLY",
        "complete_research_evidence": False,
        "complete_research_evidence_download": "Use the separate Download Everything export.",
        "intentionally_excluded_evidence": [
            "raw market and microstructure streams",
            "complete order, fill, lifecycle and trade ledgers",
            "complete shadow and counterfactual evidence",
            "complete analyzer report set and historical archives",
        ],
        "start_here": start_here,
        "required_members": start_here,
        "files_included": [f["path"] for f in file_index],
        "file_index": file_index,
        "research_db": _db_stats(AGENT_ROOT / "research.db"),
        "genome_report_summary": report_summary,
        "implementation_status": _implementation_checklist(),
    }

    status_path = OUT_DIR / "_temp_implementation_status.json"
    status_path.write_text(json.dumps(manifest["implementation_status"], indent=2), encoding="utf-8")
    to_pack.append((status_path, "IMPLEMENTATION_STATUS.json"))
    file_index.append({
        "path": "IMPLEMENTATION_STATUS.json",
        "bytes": status_path.stat().st_size,
        "sha256_prefix": _file_sha256(status_path),
    })

    genome_line = (
        "  7. data/genome_analysis_report.json — latest genome analysis"
        if report_path.is_file()
        else "  7. Genome report unavailable in this generation; see manifest summary."
    )
    readme = f"""GPT Architecture Audit Bundle — Trading Genome v11
Generated: {stamp}
Architecture: {ARCHITECTURE_VERSION}

PURPOSE: SOURCE AND ARCHITECTURE REVIEW.

This is NOT the complete research-evidence download. It may contain an optional
derived genome summary when that artifact exists, but it does not promise the
raw market, order, fill, lifecycle, trade, shadow, counterfactual, historical,
or complete analyzer-report evidence needed to reproduce research conclusions.
Use the separate Download Everything export for complete evidence analysis.

Upload this ZIP when reviewing implementation architecture and source wiring.

Start with:
  1. GPT_AUDIT_MANIFEST.json — versions, file list, DB stats
  2. IMPLEMENTATION_STATUS.json — what's implemented vs pending
  3. source/bot.py — main execution engine (Scenario C)
  4. source/analyzer_research_engine_v62.py — production analyzer loop
  5. source/research/genome/ — DNA-first persistent genome pipeline
  6. source/research_genome/ — bot-side recorders
{genome_line}

Key philosophy:
  Bot records facts → research.db → Genome Memory → Discoveries → Recommendations → Human decides
  Analyzer NEVER changes execution automatically.

This bundle auto-regenerates every analyzer cycle (~30 min).
"""
    readme_path = OUT_DIR / "_temp_readme.txt"
    readme_path.write_text(readme, encoding="utf-8")
    to_pack.append((readme_path, "README.txt"))
    file_index.append({
        "path": "README.txt",
        "bytes": readme_path.stat().st_size,
        "sha256_prefix": _file_sha256(readme_path),
    })

    manifest_path = OUT_DIR / MANIFEST_NAME
    manifest["files_included"] = sorted(
        {record["path"] for record in file_index} | {MANIFEST_NAME}
    )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    to_pack.append((manifest_path, MANIFEST_NAME))

    _validate_required_and_promised_members(to_pack, manifest)

    out_zip = OUT_DIR / ZIP_NAME
    _atomic_zip_build(to_pack, out_zip)

    for t in (readme_path, status_path):
        t.unlink(missing_ok=True)

    manifest["zip_path"] = str(out_zip.resolve())
    manifest["zip_size_mb"] = round(out_zip.stat().st_size / (1024 * 1024), 2)
    manifest["zip_verified"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_zip, manifest


if __name__ == "__main__":
    path, meta = build()
    print(path)
    print(json.dumps(meta, indent=2))

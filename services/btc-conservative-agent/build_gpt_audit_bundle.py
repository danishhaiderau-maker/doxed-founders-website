#!/usr/bin/env python3
"""
GPT Audit Bundle — one-click export for ChatGPT review.

Includes main bot script, legacy + genome analyzers, recorder modules,
latest genome report, version sync, and implementation checklist.

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

SOURCE_FILES = (
    "bot.py",
    "btc_conservative_agent.py",
    "pathway_lab_validation.py",
    "scenario_c_config.py",
    "research/analyzer_research_engine_v62.py",
    "research/research_dashboard.py",
)

GENOME_GLOB = "research/genome/*.py"
RECORDER_GLOB = "research_genome/*.py"

DATA_FILES = (
    "research/genome/genome_analysis_report.json",
    "research/genome/genome_library.json",
    "research/genome/genome_discoveries.json",
    "bot_analyzer_sync.json",
    "repo_version_sync.json",
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
            "decision_genome_full": {"status": "PARTIAL", "note": "counts only; Priority 4"},
            "outcome_genome_full": {"status": "PARTIAL", "note": "fingerprints active"},
            "replay_audit": {"status": "NOT_STARTED", "note": "Priority 5"},
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

    for rel in SOURCE_FILES:
        src = AGENT_ROOT / rel
        if src.is_file():
            arc = f"source/{rel.replace(chr(92), '/')}"
            to_pack.append((src, arc))
            file_index.append({
                "path": arc,
                "bytes": src.stat().st_size,
                "sha256_prefix": _file_sha256(src),
            })

    for pattern in (GENOME_GLOB, RECORDER_GLOB):
        base = AGENT_ROOT / pattern.split("/")[0]
        sub = pattern.split("/", 1)[1] if "/" in pattern else "*.py"
        for src in sorted(base.glob(sub)):
            if not src.is_file() or src.suffix != ".py":
                continue
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
            file_index.append({"path": arc, "bytes": src.stat().st_size})

    repo_root = AGENT_ROOT.parent.parent
    for src_rel, arc in DOC_FILES:
        src = (AGENT_ROOT / src_rel).resolve()
        if not src.is_file():
            src = repo_root / "docs" / Path(arc).name
        if src.is_file():
            to_pack.append((src, arc))

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

    manifest: Dict[str, Any] = {
        "schema": "gpt_audit_bundle_manifest_v1",
        "generated_at": stamp,
        "architecture_version": ARCHITECTURE_VERSION,
        "purpose": "Upload to ChatGPT for full-stack genome architecture audit",
        "start_here": [
            "GPT_AUDIT_MANIFEST.json",
            "README.txt",
            "IMPLEMENTATION_STATUS.json",
            "source/bot.py",
            "source/research/analyzer_research_engine_v62.py",
            "source/research/genome/run_analyzer.py",
            "data/genome_analysis_report.json",
        ],
        "files_included": [f["path"] for f in file_index],
        "file_index": file_index,
        "research_db": _db_stats(AGENT_ROOT / "research.db"),
        "genome_report_summary": report_summary,
        "implementation_status": _implementation_checklist(),
    }

    status_path = OUT_DIR / "_temp_implementation_status.json"
    status_path.write_text(json.dumps(manifest["implementation_status"], indent=2), encoding="utf-8")
    to_pack.append((status_path, "IMPLEMENTATION_STATUS.json"))

    readme = f"""GPT Audit Bundle — Trading Genome v11
Generated: {stamp}
Architecture: {ARCHITECTURE_VERSION}

UPLOAD THIS ENTIRE ZIP TO CHATGPT.

Start with:
  1. GPT_AUDIT_MANIFEST.json — versions, file list, DB stats
  2. IMPLEMENTATION_STATUS.json — what's implemented vs pending
  3. source/bot.py — main execution engine (Scenario C)
  4. source/research/analyzer_research_engine_v62.py — legacy analyzer loop
  5. source/research/genome/ — DNA-first persistent genome pipeline
  6. source/research_genome/ — bot-side recorders
  7. data/genome_analysis_report.json — latest genome analysis

Key philosophy:
  Bot records facts → research.db → Genome Memory → Discoveries → Recommendations → Human decides
  Analyzer NEVER changes execution automatically.

This bundle auto-regenerates every analyzer cycle (~30 min).
"""
    readme_path = OUT_DIR / "_temp_readme.txt"
    readme_path.write_text(readme, encoding="utf-8")
    to_pack.append((readme_path, "README.txt"))

    manifest_path = OUT_DIR / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    to_pack.append((manifest_path, MANIFEST_NAME))

    if not any(f[1].startswith("source/bot.py") for f in to_pack):
        raise FileNotFoundError("bot.py missing — run from btc-conservative-agent root")

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

"""Read-only Fly probe: research-reset diagnostics and research_events_v22 seal state."""
import json
import os
import re
from pathlib import Path

root = Path("/app/data/runtime")
out = {"schema": "fly_reset_seal_probe_v1"}

receipts = root / "research_reset_receipts"
diagnostics = {}
for sub in ("_preflight", "_progress"):
    directory = receipts / sub
    rows = []
    if directory.is_dir():
        for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime)[-5:]:
            if path.is_file() and path.stat().st_size < 65536:
                try:
                    rows.append({"name": path.name, "body": json.loads(path.read_text("utf-8"))})
                except Exception as exc:
                    rows.append({"name": path.name, "error": type(exc).__name__})
    diagnostics[sub] = rows
out["reset_diagnostics"] = diagnostics
out["active_reset_pointer_present"] = (receipts / "ACTIVE_RESET.json").is_file()

seal_dir = root / "research_events_v22.seals"
seals = []
if seal_dir.is_dir():
    for path in sorted(seal_dir.iterdir()):
        if path.is_dir():
            seals.append({"name": path.name, "directory": True,
                          "children": sorted(child.name for child in path.rglob("*"))[:50]})
            continue
        row = {"name": path.name, "bytes": path.stat().st_size}
        match = re.fullmatch(r"generation-([1-9][0-9]*)\.json", path.name)
        if match:
            try:
                receipt = json.loads(path.read_text("utf-8"))
                row.update({key: receipt.get(key) for key in
                            ("generation", "relative_path", "size_bytes", "sha256", "row_count", "state")})
            except Exception as exc:
                row["error"] = type(exc).__name__
            sealed = root / f"research_events_v22.jsonl.{match.group(1)}"
            row["sealed_file_present"] = sealed.is_file()
            row["sealed_file_bytes"] = sealed.stat().st_size if sealed.is_file() else None
        seals.append(row)
out["seals"] = seals
out["v22_files"] = sorted((path.name, path.stat().st_size)
                         for path in root.glob("research_events_v22*") if path.is_file())

log_lines = []
log = Path("/app/data/bot.log")
if log.is_file():
    pattern = re.compile(r"RESEARCH RESET|WIPE FLY|LIFECYCLE PIPELINE|V22_SEAL|fresh_collection|EPOCH BOUNDARY|epoch_boundary")
    size = log.stat().st_size
    with log.open("rb") as handle:
        handle.seek(max(0, size - 64 * 1024 * 1024))
        for raw in handle:
            line = raw.decode("utf-8", "replace").rstrip()
            if pattern.search(line):
                log_lines.append(line[:400])
out["bot_log_matches"] = log_lines[-80:]
out["source_git_rev"] = os.getenv("SOURCE_GIT_REV")
print(json.dumps(out, sort_keys=True, indent=1, default=str))

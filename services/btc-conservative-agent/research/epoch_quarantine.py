"""Non-destructive, rollback-capable research epoch quarantine."""
from __future__ import annotations
import hashlib, json, os, shutil, uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "research_epoch_quarantine_v1"

def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def quarantine_epoch(root, paths, archive_root="research_epoch_quarantine", cutoff=None):
    root = Path(root).resolve()
    cutoff = cutoff or datetime.now(timezone.utc).isoformat()
    epoch_id = "epoch_" + cutoff.replace(":", "").replace("-", "").replace("+", "_") + "_" + uuid.uuid4().hex[:8]
    destination = (root / archive_root / epoch_id).resolve()
    if root not in destination.parents: raise ValueError("unsafe quarantine root")
    destination.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for raw in sorted(set(str(p) for p in paths if p)):
            source = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
            if root not in source.parents or not source.is_file(): continue
            relative = source.relative_to(root)
            target = destination / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            size, digest = source.stat().st_size, _sha256(source)
            os.replace(source, target)
            if target.stat().st_size != size or _sha256(target) != digest:
                raise RuntimeError(f"quarantine verification failed: {relative.as_posix()}")
            moved.append((source, target, relative, size, digest))
        manifest = {"schema": SCHEMA, "complete": True, "epoch_id": epoch_id,
            "cutoff_utc": cutoff, "source_root": ".", "file_count": len(moved),
            "files": [{"path": r.as_posix(), "size_bytes": s, "sha256": h}
                      for _, _, r, s, h in moved]}
        temp = destination / ".manifest.tmp"
        temp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, destination / "quarantine_manifest.json")
        return {"epoch_id": epoch_id, "path": str(destination), "cutoff_utc": cutoff,
                "moved": [r.as_posix() for _, _, r, _, _ in moved], "errors": []}
    except Exception:
        for source, target, _, _, _ in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not source.exists(): os.replace(target, source)
        raise


def purge_quarantine_archives(root, extra_roots=None):
    """Permanently delete quarantine / archive trees so infected fills cannot be restored."""
    root = Path(root).resolve()
    names = (
        "research_epoch_quarantine",
        "epoch_quarantine",
        "research_archive",
        "research_session_archives",
    )
    targets = [root / name for name in names]
    genome = root / "research" / "genome" / "epoch_quarantine"
    targets.append(genome)
    for extra in extra_roots or ():
        if extra:
            targets.append(Path(extra))
    deleted_trees = []
    deleted_files = 0
    errors = []
    seen = set()
    for target in targets:
        try:
            resolved = target.resolve()
        except OSError as exc:
            errors.append("%s: %s" % (target, exc))
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if root not in resolved.parents and resolved != root:
            errors.append("%s: refuses to delete outside runtime root" % resolved)
            continue
        if not resolved.exists():
            continue
        file_count = 0
        if resolved.is_dir():
            for _, _, files in os.walk(resolved):
                file_count += len(files)
            try:
                shutil.rmtree(resolved)
            except OSError as exc:
                errors.append("%s: %s" % (resolved, exc))
                continue
        elif resolved.is_file():
            file_count = 1
            try:
                resolved.unlink()
            except OSError as exc:
                errors.append("%s: %s" % (resolved, exc))
                continue
        deleted_trees.append(str(resolved))
        deleted_files += file_count
    return {"deleted_trees": deleted_trees, "deleted_files": deleted_files, "errors": errors}

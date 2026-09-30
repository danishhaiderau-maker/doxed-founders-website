"""Stage a complete segment shadow mirror for canonical promotion.

The analyzer reads only a promoted canonical store
(``canonical_dataset_current.json``), produced by
``scripts/migrate_canonical_research_store.py`` from a mirror that carries a
verified ``.fly-sync-state.json`` and a sync heartbeat. The segment puller's
shadow tree has neither, so this tool copies the tree into a separate view and
emits both, fail-closed: it refuses unless the shadow covers everything Fly has
published at the deployed revision.

    python research_segment_promotion.py --view C:\\DoxxedCrypto\\segment-promotion-view
    python ..\\..\\scripts\\migrate_canonical_research_store.py --source <view> \\
        --heartbeat <view>\\.segment-promotion.heartbeat.json --destination <checkout>\\...\\canonical-research-data

The live shadow tree is never written; the puller's run lock is held while
copying so no segment is applied mid-copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from research_segment_puller import STATE_SCHEMA, PullerError, _RunLock, refuse_unsafe_root
from research_segment_store import HttpSegmentSource, StoreError

HEARTBEAT_NAME = ".segment-promotion.heartbeat.json"
SYNC_STATE_NAME = ".fly-sync-state.json"
RECEIPT_SCHEMA = "research_segment_promotion_view_v1"


class PromotionRefused(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deny_reasons(state: dict, head: dict, health: dict, tree: Path) -> list[str]:
    reasons = []
    if state.get("schema") != STATE_SCHEMA:
        reasons.append("PULLER_STATE_MISSING")
        return reasons
    applied = int(state.get("applied_seq") or 0)
    published = int(head.get("published_seq") or 0)
    if applied < 1 or applied != published:
        reasons.append(f"SHADOW_BEHIND_PUBLISHED:{applied}<{published}")
    if head.get("last_manifest_sha256") != state.get("last_manifest_sha256"):
        reasons.append("HEAD_MANIFEST_MISMATCH")
    if int(head.get("unshipped_bytes") or 0) != 0:
        reasons.append(f"FLY_UNSHIPPED_BYTES:{int(head.get('unshipped_bytes') or 0)}")
    for field in ("oversized_paths", "racing_paths"):
        if head.get(field):
            reasons.append(f"FLY_{field.upper()}:{len(head[field])}")
    if head.get("shipper_last_error"):
        reasons.append(f"FLY_SHIPPER_ERROR:{head['shipper_last_error']}")
    shipped_rev = str(state.get("last_source_git_rev") or "").lower()
    deployed_rev = str(health.get("source_git_rev") or "").lower()
    if not shipped_rev or not deployed_rev or not shipped_rev.startswith(deployed_rev):
        reasons.append(f"REVISION_MISMATCH:{shipped_rev[:12] or '-'}!={deployed_rev or '-'}")
    if not str(health.get("tile_registry_signature") or "").strip():
        reasons.append("TILE_SIGNATURE_MISSING")
    if not (tree / "research_session.json").is_file():
        reasons.append("RESEARCH_SESSION_MISSING")
    return reasons


def stage_view(*, shadow_root: Path, view_root: Path, head: dict, health: dict) -> dict:
    shadow_root = refuse_unsafe_root(shadow_root, "shadow root")
    view_root = refuse_unsafe_root(view_root, "promotion view")
    tree = shadow_root / "tree"
    if view_root.resolve() == tree.resolve() or tree.resolve() in view_root.resolve().parents:
        raise PromotionRefused(["VIEW_INSIDE_SHADOW_TREE"])
    if view_root.exists() and any(view_root.iterdir()):
        raise PromotionRefused(["VIEW_NOT_EMPTY"])
    lock = _RunLock(shadow_root / ".puller" / "run.lock")
    try:
        state_path = shadow_root / ".puller" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        reasons = deny_reasons(state, head, health, tree)
        if reasons:
            raise PromotionRefused(reasons)
        sync_state, byte_count = {}, 0
        for source in sorted(path for path in tree.rglob("*") if path.is_file()):
            relative = source.relative_to(tree).as_posix()
            target = view_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            size = target.stat().st_size
            sync_state[relative] = {"size": size, "sha256": _sha256_file(target)}
            byte_count += size
    finally:
        lock.release()
    revision = str(state["last_source_git_rev"]).lower()
    heartbeat = {
        "ok": True, "inProgress": False, "phase": "complete",
        "source": "research_segments_volume_sink", "syncedAt": state.get("last_applied_at"),
        "sourceRevision": revision, "mirroredSourceRevision": revision,
        "deployedRevision": str(health["source_git_rev"]).lower(), "revisionParity": "MATCH",
        "tileRegistrySignature": health["tile_registry_signature"],
        "collectionEpochId": state.get("last_collection_epoch_id"),
        "segmentAppliedSeq": state["applied_seq"],
        "segmentHeadManifestSha256": state["last_manifest_sha256"],
        "throttledSnapshots": head.get("throttled_snapshots") or [],
        "fileCount": len(sync_state),
    }
    (view_root / SYNC_STATE_NAME).write_text(json.dumps(sync_state, sort_keys=True, indent=1), encoding="utf-8")
    (view_root / HEARTBEAT_NAME).write_text(json.dumps(heartbeat, sort_keys=True, indent=2), encoding="utf-8")
    return {"schema": RECEIPT_SCHEMA, "staged_at": _utc_now(), "view": str(view_root),
            "files": len(sync_state), "bytes": byte_count, "applied_seq": state["applied_seq"],
            "head_manifest_sha256": state["last_manifest_sha256"], "source_revision": revision}


def _health(base_url: str) -> dict:
    with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=30) as response:
        return json.loads(response.read())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-root", default=r"C:\DoxxedCrypto\fly-mirror-segments")
    parser.add_argument("--view", required=True)
    parser.add_argument("--base-url", default=os.getenv("RESEARCH_SEGMENTS_BASE_URL")
                        or "https://doxed-btc-bot.fly.dev")
    args = parser.parse_args(argv)
    try:
        source = HttpSegmentSource(base_url=args.base_url,
                                   admin_token=os.environ.get("BOT_ADMIN_TOKEN") or "")
        receipt = stage_view(shadow_root=Path(args.shadow_root), view_root=Path(args.view),
                             head=source.head(), health=_health(args.base_url))
    except PromotionRefused as exc:
        print(json.dumps({"ok": False, "deny_reasons": exc.reasons}, indent=2))
        return 3
    except (PullerError, StoreError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps({"ok": True, **receipt}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Publish the current laptop analyzer generation to the Fly analyzer mirror.

Builds an ``analyzer_mirror_bundle_v2`` ZIP from the committed
``report_manifest.json`` and POSTs it to Fly ``/api/data-sync/analyzer-report``
(the route #213 left in place when it retired the legacy sync tree). Fly then
serves the generation on ``/analysis`` and its ``/api/analyzer/summary`` and
``/api/analyzer/genome`` panels.

The deployed Fly validator accepts only ``.html/.txt/.json/.log`` members of at
most 50 MB, 150 MB expanded and 50 MB compressed, and requires the bundle to
match the embedded report manifest exactly. Reports that cannot pass those
limits (``.jsonl.gz`` shards, the ~290 MB baseline replay report) stay on the
laptop: the embedded manifest is a disclosed mirror subset whose
``mirror_publication`` block lists every excluded report with its reason and the
sha256 of the unmodified source manifest.

Never prints the admin token. Exit codes: 0 published and verified,
2 nothing publishable / input invalid, 3 upload rejected or failed after
retries, 4 uploaded but Fly does not serve the generation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = "analyzer_mirror_bundle_v2"
RECEIPT_SCHEMA = "analyzer_mirror_publish_v1"
ALLOWED_SUFFIXES = frozenset((".html", ".txt", ".json", ".log"))
MAX_MEMBER_BYTES = 50 * 1024 * 1024
# Fly allows 150 MB expanded / 50 MB compressed; keep headroom for the manifests.
EXPANDED_BUDGET_BYTES = 140 * 1024 * 1024
MAX_COMPRESSED_BYTES = 48 * 1024 * 1024
MAX_MEMBERS = 250
REQUIRED_TEXT = "analysis_dashboard.html"
DEFAULT_BASE_URL = "https://doxed-btc-bot.fly.dev"
REVISION_RE = re.compile(r"[0-9a-fA-F]{7,64}")


class PublishError(RuntimeError):
    def __init__(self, code: str, detail: str = "", exit_code: int = 2) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.exit_code = exit_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(name: str) -> bool:
    return bool(name) and name == Path(name).name and name not in (".", "..") and ":" not in name


def select_members(report_root: Path, manifest: dict[str, Any], supplemental: list[Path]) -> dict[str, Any]:
    """Choose the publishable subset; every exclusion carries a reason."""
    text = []
    for raw in manifest.get("text_artifacts") or []:
        name = str(raw or "")
        if not _safe_name(name):
            raise PublishError("UNSAFE_TEXT_ARTIFACT", name)
        text.append(name)
    if REQUIRED_TEXT not in text:
        raise PublishError("DASHBOARD_NOT_IN_MANIFEST")
    reports = manifest.get("reports") or []
    if len(reports) != int(manifest.get("report_count") or -1):
        raise PublishError("REPORT_COUNT_MISMATCH")

    included_text: list[dict[str, Any]] = []
    included_reports: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    expanded = 0

    def admit(rel: str, source: Path, declared_size: int | None) -> tuple[bool, str | None, int]:
        if not source.is_file():
            return False, "MISSING", 0
        size = source.stat().st_size
        if declared_size is not None and size != declared_size:
            raise PublishError("GENERATION_CHANGED_DURING_SNAPSHOT", rel, exit_code=3)
        if Path(rel).suffix.lower() not in ALLOWED_SUFFIXES:
            return False, "SUFFIX_NOT_ACCEPTED_BY_FLY", size
        if size > MAX_MEMBER_BYTES:
            return False, "MEMBER_OVER_50MB", size
        if expanded + size > EXPANDED_BUDGET_BYTES:
            return False, "EXPANDED_BUDGET_EXHAUSTED", size
        return True, None, size

    for name in text:
        ok, reason, size = admit(name, report_root / name, None)
        if name == REQUIRED_TEXT and not ok:
            raise PublishError("DASHBOARD_NOT_PUBLISHABLE", str(reason))
        if ok:
            expanded += size
            included_text.append({"name": name, "source": report_root / name})
        else:
            excluded.append({"path": name, "reason": reason, "size_bytes": size})
    # Smallest first so one oversized report never crowds out the rest.
    ordered = sorted(reports, key=lambda row: int((row or {}).get("size_bytes") or 0))
    for row in ordered:
        name = str((row or {}).get("file") or "")
        if not _safe_name(name):
            raise PublishError("UNSAFE_REPORT_PATH", name)
        ok, reason, size = admit(f"reports/{name}", report_root / "reports" / name, int(row.get("size_bytes") or 0))
        if ok:
            expanded += size
            included_reports.append({"row": row, "source": report_root / "reports" / name})
        else:
            excluded.append({"path": f"reports/{name}", "reason": reason, "size_bytes": size})
    for path in supplemental:
        name = path.name
        taken = {str(item["row"].get("file")) for item in included_reports}
        if not _safe_name(name) or name in taken:
            continue
        ok, reason, size = admit(f"reports/{name}", path, None)
        if ok:
            expanded += size
            included_reports.append({"row": {"file": name, "size_bytes": size, "supplemental": True,
                                             "source": str(path)}, "source": path})
        else:
            excluded.append({"path": f"reports/{name}", "reason": reason, "size_bytes": size, "supplemental": True})
    if 1 + len(included_text) + len(included_reports) + 1 > MAX_MEMBERS:
        raise PublishError("TOO_MANY_MEMBERS")
    return {"text": included_text, "reports": included_reports, "excluded": excluded, "expanded_bytes": expanded}


def build_bundle(report_root: Path, work: Path, *, source_data_revision: str | None = None,
                 supplemental: list[Path] | None = None) -> dict[str, Any]:
    """Snapshot the committed generation into ``work`` and write the ZIP."""
    manifest_path = report_root / "report_manifest.json"
    if not manifest_path.is_file():
        raise PublishError("REPORT_MANIFEST_MISSING", str(manifest_path))
    source_bytes = manifest_path.read_bytes()
    manifest = json.loads(source_bytes.decode("utf-8"))
    provenance = manifest.get("analysis_provenance") or {}
    generated_at = str(manifest.get("generated_at") or "")
    revision = str(provenance.get("generation_revision") or "")
    data_revision = str(source_data_revision or manifest.get("source_revision")
                        or manifest.get("source_data_revision") or manifest.get("deployed_revision") or "")
    for key, value in (("generated_at", generated_at), ("analyzer_sync_id", manifest.get("analyzer_sync_id")),
                       ("analyzer_version", manifest.get("analyzer_version")),
                       ("cohort_schema", provenance.get("cohort_schema")), ("data_scope", manifest.get("data_scope"))):
        if not str(value or "").strip():
            raise PublishError("PROVENANCE_INCOMPLETE", key)
    if not REVISION_RE.fullmatch(revision):
        raise PublishError("GENERATION_REVISION_INVALID", revision)
    if not REVISION_RE.fullmatch(data_revision):
        raise PublishError("SOURCE_DATA_REVISION_INVALID", data_revision)
    datetime.fromisoformat(generated_at.replace("Z", "+00:00"))

    picked = select_members(report_root, manifest, list(supplemental or []))
    snapshot = work / "snapshot"
    (snapshot / "reports").mkdir(parents=True)
    files: list[dict[str, Any]] = []

    def snap(rel: str, source: Path) -> dict[str, Any]:
        target = snapshot / rel
        shutil.copyfile(source, target)
        return {"path": rel, "size_bytes": target.stat().st_size, "sha256": _sha256_file(target)}

    for item in picked["text"]:
        files.append(snap(item["name"], item["source"]))
    report_rows = []
    for item in picked["reports"]:
        row = dict(item["row"])
        entry = snap(f"reports/{row['file']}", item["source"])
        if not row.get("supplemental") and entry["size_bytes"] != int(row.get("size_bytes") or -1):
            raise PublishError("GENERATION_CHANGED_DURING_SNAPSHOT", row["file"], exit_code=3)
        files.append(entry)
        report_rows.append(row)
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != hashlib.sha256(source_bytes).hexdigest():
        raise PublishError("GENERATION_CHANGED_DURING_SNAPSHOT", "report_manifest.json", exit_code=3)

    mirror_manifest = dict(manifest)
    mirror_manifest["text_artifacts"] = [item["name"] for item in picked["text"]]
    mirror_manifest["reports"] = report_rows
    mirror_manifest["report_count"] = len(report_rows)
    mirror_manifest["mirror_publication"] = {
        "schema": "analyzer_mirror_subset_v1",
        "source_report_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_report_count": len(manifest.get("reports") or []),
        "source_text_artifact_count": len(manifest.get("text_artifacts") or []),
        "published_report_count": len(report_rows),
        "excluded": picked["excluded"],
        "note": "Reports Fly cannot accept (suffix/size limits) remain on the laptop analyzer :9001.",
    }
    mirror_bytes = json.dumps(mirror_manifest, indent=2, sort_keys=False).encode("utf-8")
    (snapshot / "report_manifest.json").write_bytes(mirror_bytes)
    files.insert(0, {"path": "report_manifest.json", "size_bytes": len(mirror_bytes),
                     "sha256": hashlib.sha256(mirror_bytes).hexdigest()})

    stamp = generated_at.replace(":", "").replace("-", "")
    bundle_manifest = {
        "schema": SCHEMA,
        "snapshot_id": f"analyzer-{stamp}-{uuid.uuid4().hex}",
        "analyzer_run_id": str(manifest.get("analyzer_sync_id")),
        "analyzer_version": str(manifest.get("analyzer_version")),
        "analyzer_generated_at": generated_at,
        "source_data_revision": data_revision,
        "analyzer_generation_revision": revision,
        "cohort_schema": str(provenance.get("cohort_schema")),
        "data_scope": str(manifest.get("data_scope")),
        "session_scope": str(manifest.get("session_scope") or ""),
        "generation_id": str(manifest.get("generation_id") or ""),
        "source_report_manifest_sha256": hashlib.sha256(mirror_bytes).hexdigest(),
        "original_report_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "mirror_subset": bool(picked["excluded"]),
        "excluded_report_count": len(picked["excluded"]),
        "publisher": "scripts/publish_analyzer_mirror.py",
        "files": files,
    }
    bundle_path = work / "analyzer_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for row in files:
            archive.write(snapshot / row["path"], row["path"])
        archive.writestr("bundle_manifest.json", json.dumps(bundle_manifest, indent=2))
    size = bundle_path.stat().st_size
    if size > MAX_COMPRESSED_BYTES:
        raise PublishError("BUNDLE_OVER_COMPRESSED_LIMIT", str(size))
    return {"bundle_path": bundle_path, "bundle_sha256": _sha256_file(bundle_path), "bundle_bytes": size,
            "manifest": bundle_manifest, "expanded_bytes": picked["expanded_bytes"] + len(mirror_bytes),
            "excluded": picked["excluded"]}


def _multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = f"----analyzer-{uuid.uuid4().hex}"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/zip\r\n\r\n").encode("ascii")
    return head + payload + f"\r\n--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


Opener = Callable[[urllib.request.Request, float], Any]


def _default_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https base URL


def _request_json(opener: Opener, request: urllib.request.Request, timeout: float) -> tuple[int, dict[str, Any]]:
    try:
        with opener(request, timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        status, body = int(exc.code), exc.read(64 * 1024)
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError):
        parsed = {"raw": body[:300].decode("utf-8", "replace")}
    return status, parsed if isinstance(parsed, dict) else {"value": parsed}


def upload(base_url: str, token: str, bundle: Path, *, attempts: int = 4, timeout: float = 180.0,
           opener: Opener = _default_open, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    body, content_type = _multipart("bundle", "analyzer_bundle.zip", bundle.read_bytes())
    last: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            f"{base_url}/api/data-sync/analyzer-report", data=body, method="POST",
            headers={"Content-Type": content_type, "X-Bot-Admin-Token": token, "User-Agent": "analyzer-mirror-publisher"})
        try:
            status, parsed = _request_json(opener, request, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status, parsed = 0, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        last = {"attempt": attempt, "status": status, "response": parsed}
        if status == 200 and parsed.get("ok") is True:
            return last
        # A validation rejection is final; only transport/server failures retry.
        if status in (400, 401, 403, 404, 410, 413):
            break
        if attempt < attempts:
            sleep(min(120.0, 10.0 * 2 ** (attempt - 1)))
    raise PublishError("UPLOAD_FAILED", json.dumps(last)[:400], exit_code=3)


def verify(base_url: str, token: str, generated_at: str, *, opener: Opener = _default_open,
           timeout: float = 30.0) -> dict[str, Any]:
    """Confirm both public Fly panels now serve this exact generation."""
    panels = {}
    for endpoint in ("summary", "genome"):
        request = urllib.request.Request(f"{base_url}/api/analyzer/{endpoint}",
                                         headers={"User-Agent": "analyzer-mirror-publisher", "Cache-Control": "no-cache"})
        status, payload = _request_json(opener, request, timeout)
        mirror = payload.get("mirror_status") or {}
        panels[endpoint] = {
            "status": status, "mirror_available": payload.get("mirror_available"),
            "analyzer_generated_at": mirror.get("analyzer_generated_at"), "generation": mirror.get("generation"),
            "current": payload.get("mirror_available") is True and mirror.get("analyzer_generated_at") == generated_at,
        }
    request = urllib.request.Request(f"{base_url}/api/analyzer-mirror/status",
                                     headers={"X-Bot-Admin-Token": token, "User-Agent": "analyzer-mirror-publisher"})
    status, payload = _request_json(opener, request, timeout)
    return {"panels": panels, "mirror_status_http": status, "mirror_status_available": payload.get("available"),
            "verified": all(row["current"] for row in panels.values())}


def read_token(vault_env: Path | None) -> str:
    token = os.environ.get("BOT_ADMIN_TOKEN", "").strip()
    if token or vault_env is None or not vault_env.is_file():
        return token
    for line in vault_env.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*BOT_ADMIN_TOKEN\s*=\s*(.+)$", line)
        if match:
            return match.group(1).strip().strip('"').strip("'")
    return ""


def write_receipt(path: Path | None, receipt: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def publish(args: argparse.Namespace, *, opener: Opener = _default_open,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    report_root = Path(args.report_root)
    receipt: dict[str, Any] = {"schema": RECEIPT_SCHEMA, "started_at": utc_now(), "base_url": args.base_url,
                               "report_root": str(report_root), "state": "FAILED"}
    previous = {}
    if args.receipt and Path(args.receipt).is_file():
        try:
            previous = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="analyzer-mirror-", dir=args.work_dir or None))
    try:
        supplemental = [Path(p) for p in (args.supplemental or []) if Path(p).is_file()]
        built = build_bundle(report_root, work, source_data_revision=args.source_data_revision,
                             supplemental=supplemental)
        manifest = built["manifest"]
        receipt.update({
            "analyzer_generated_at": manifest["analyzer_generated_at"], "generation_id": manifest["generation_id"],
            "analyzer_generation_revision": manifest["analyzer_generation_revision"],
            "source_data_revision": manifest["source_data_revision"], "bundle_sha256": built["bundle_sha256"],
            "bundle_bytes": built["bundle_bytes"], "expanded_bytes": built["expanded_bytes"],
            "file_count": len(manifest["files"]), "excluded": built["excluded"],
        })
        if (not args.force and previous.get("state") == "PUBLISHED"
                and previous.get("analyzer_generated_at") == manifest["analyzer_generated_at"]):
            receipt.update({"state": "ALREADY_PUBLISHED", "fly_generation": previous.get("fly_generation")})
            return receipt
        if args.dry_run:
            receipt["state"] = "DRY_RUN"
            return receipt
        token = read_token(Path(args.vault_env) if args.vault_env else None)
        if not token:
            raise PublishError("BOT_ADMIN_TOKEN_UNAVAILABLE")
        uploaded = upload(args.base_url, token, built["bundle_path"], attempts=args.attempts, opener=opener, sleep=sleep)
        receipt["upload"] = {"attempt": uploaded["attempt"], "status": uploaded["status"]}
        receipt["fly_generation"] = uploaded["response"].get("generation")
        receipt["state"] = "UPLOADED_UNVERIFIED"
        check: dict[str, Any] = {}
        for _ in range(max(1, args.verify_attempts)):
            check = verify(args.base_url, token, manifest["analyzer_generated_at"], opener=opener)
            if check["verified"]:
                break
            sleep(5.0)
        receipt["verification"] = check
        if not check["verified"]:
            raise PublishError("FLY_PANELS_NOT_CURRENT", json.dumps(check["panels"])[:300], exit_code=4)
        receipt["state"] = "PUBLISHED"
        receipt["published_at"] = utc_now()
        return receipt
    except PublishError as exc:
        receipt.update({"error": exc.code, "detail": exc.detail[:400], "exit_code": exc.exit_code})
        if previous.get("state") == "PUBLISHED":
            receipt["last_published"] = {k: previous.get(k) for k in
                                         ("analyzer_generated_at", "published_at", "fly_generation", "bundle_sha256")}
        elif previous.get("last_published"):
            receipt["last_published"] = previous["last_published"]
        return receipt
    finally:
        receipt["finished_at"] = utc_now()
        receipt["duration_sec"] = round(time.monotonic() - started, 1)
        shutil.rmtree(work, ignore_errors=True)
        write_receipt(Path(args.receipt) if args.receipt else None, receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report-root", required=True, help="analyzer output dir holding report_manifest.json")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--vault-env", default="", help="env file with BOT_ADMIN_TOKEN (used if env var unset)")
    parser.add_argument("--receipt", default="", help="JSON receipt path (laptop-chain state dir)")
    parser.add_argument("--source-data-revision", default="")
    parser.add_argument("--supplemental", action="append", help="extra report file published under reports/")
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--verify-attempts", type=int, default=6)
    parser.add_argument("--force", action="store_true", help="republish even if this generation is already published")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.base_url = args.base_url.rstrip("/")
    receipt = publish(args)
    summary = {k: receipt.get(k) for k in ("state", "analyzer_generated_at", "fly_generation", "bundle_bytes",
                                           "file_count", "error", "detail", "duration_sec")}
    summary["excluded"] = len(receipt.get("excluded") or [])
    print(json.dumps(summary))
    if receipt["state"] in ("PUBLISHED", "ALREADY_PUBLISHED", "DRY_RUN"):
        return 0
    return int(receipt.get("exit_code") or 3)


if __name__ == "__main__":
    sys.exit(main())

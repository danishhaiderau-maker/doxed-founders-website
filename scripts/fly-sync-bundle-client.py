"""Bounded direct-Fly transport adapter; stdout contains verified staging only."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
from data_sync_bundle_client import fetch_verified_package

MAX_INPUT = 32 * 1024 * 1024
MAX_META = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the admin credential to another URL.


def run(request, *, emit, fetch=None, sleep=time.sleep, clock=time.monotonic):
    started = clock()
    if request.get("source_url", "").rstrip("/") != "https://doxed-btc-bot.fly.dev":
        raise ValueError("NON_CANONICAL_SOURCE")
    manifest = request["manifest"]
    # Public manifest authority uses inventory_* names. Internal worker
    # metadata uses ack_eligible; never accept that alias instead of the
    # authenticated public manifest's explicit CURRENT/authority gates.
    if (not isinstance(manifest, dict)
            or manifest.get("schema") != "fly_runtime_incremental_sync_v1"
            or manifest.get("inventory_status") != "CURRENT"
            or manifest.get("inventory_authoritative") is not True
            or manifest.get("inventory_ack_eligible") is not True
            or ("ack_eligible" in manifest and manifest["ack_eligible"] is not True)):
        raise ValueError("MANIFEST_NOT_ACK_ELIGIBLE")
    generation = {key: manifest.get(key) for key in (
        "inventory_generation_id", "inventory_sha256", "source_git_rev",
        "collection_epoch_id", "tile_registry_signature")}
    if (any(not isinstance(value, str) or not value for value in generation.values())
            or not re.fullmatch(r"[0-9a-f]{64}", generation["inventory_generation_id"])
            or generation["inventory_sha256"] != generation["inventory_generation_id"]
            or ("generation_id" in manifest
                and manifest["generation_id"] != generation["inventory_generation_id"])):
        raise ValueError("MANIFEST_GENERATION_INVALID")
    generation["ack_eligible"] = True
    rows = manifest.get("files")
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("MANIFEST_ROW_LIMIT")
    original = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] in original:
            raise ValueError("MANIFEST_DUPLICATE_OR_INVALID_ROW")
        original[row["path"]] = row
    if fetch is None:
        token = request.get("admin_token")
        if not isinstance(token, str) or not token:
            raise ValueError("ADMIN_TOKEN_REQUIRED")
        opener = build_opener(NoRedirect())
        def fetch(relative, *, timeout):
            if not relative.startswith("/api/data-sync/") or "#" in relative:
                raise ValueError("NON_CANONICAL_REQUEST_PATH")
            call = Request("https://doxed-btc-bot.fly.dev" + relative,
                           headers={"X-Bot-Admin-Token": token, "Accept-Encoding": "identity"})
            try:
                response = opener.open(call, timeout=min(30, timeout))
            except HTTPError as error:
                with error:
                    return error.code, dict(error.headers), b""  # Never echo response diagnostics.
            with response:
                limit = MAX_META if ("descriptor=1" in relative or "/bundles?" in relative) else 1024 * 1024
                return response.status, dict(response.headers), response.read(limit + 1)
    index_url = "/api/data-sync/bundles?" + urlencode({
        "generation_id": generation["inventory_generation_id"]})
    delay, pressure_failures = 5, 0
    accepted_prefix = []
    seen_paths = set()
    def consume(entry, building):
        remaining = (600 if building else 1800) - (clock() - started)
        if remaining <= 0:
            raise ValueError("BUNDLE_INDEX_PREPARATION_DEADLINE" if building else "BUNDLE_TRANSFER_DEADLINE")
        staged = fetch_verified_package(entry, generation, original, request["staging_root"], fetch,
            deadline_sec=min(120, remaining), clock=clock, sleep=sleep,
            verified_local_root=request.get("verified_local_root"))
        for member in staged["members"]:
            if member["path"] in seen_paths:
                raise ValueError("BUNDLE_MEMBER_REPEATED_ACROSS_PACKAGES")
            seen_paths.add(member["path"])
        emit({"schema":"fly_bundle_staging_receipt_v1", "status":"PACKAGE_VERIFIED",
              "generation":generation, **staged})
        remaining = (600 if building else 1800) - (clock() - started)
        if remaining <= 0:
            raise ValueError("BUNDLE_INDEX_PREPARATION_DEADLINE" if building else "BUNDLE_TRANSFER_DEADLINE")
        sleep(min(0.5, remaining))
    while True:
        elapsed = clock() - started
        if elapsed >= 600:
            raise ValueError("BUNDLE_INDEX_PREPARATION_DEADLINE")
        try:
            status, headers, body = fetch(index_url, timeout=min(30, 600-elapsed))
        except (TimeoutError, ConnectionError, URLError):
            status, headers, body = 503, {}, b""
        elapsed = clock() - started
        if elapsed >= 600:
            raise ValueError("BUNDLE_INDEX_PREPARATION_DEADLINE")
        if not isinstance(body, bytes) or len(body) > MAX_META:
            raise ValueError("BUNDLE_INDEX_LIMIT")
        package_count = None
        if status == 200:
            index = json.loads(body)
            if (not isinstance(index, dict)
                    or index.get("schema") != "fly_runtime_transport_bundle_index_v1"
                    or index.get("status") not in ("COMPLETE", "BUILDING")
                    or index.get("generation_id") != generation["inventory_generation_id"]
                    or index.get("ack_authority") != "ORIGINAL_MANIFEST_ROWS_ONLY"
                    or not isinstance(index.get("generation"), dict)
                    or any(index["generation"].get(key) != value
                           for key, value in generation.items() if key != "ack_eligible")):
                raise ValueError("BUNDLE_INDEX_NOT_COMPLETE_OR_MATCHED")
            packages = index.get("packages")
            if not isinstance(packages, list) or len(packages) > 4096:
                raise ValueError("BUNDLE_INDEX_LIMIT")
            package_count = len(packages)
            digests = [entry.get("package_sha256") if isinstance(entry, dict) else None for entry in packages]
            if any(not isinstance(d, str) for d in digests) or len(set(digests)) != len(digests):
                raise ValueError("BUNDLE_INDEX_DUPLICATE")
            if len(packages) < len(accepted_prefix) or packages[:len(accepted_prefix)] != accepted_prefix:
                raise ValueError("BUNDLE_INDEX_PREFIX_CHANGED")
            for entry in packages[len(accepted_prefix):]:
                consume(entry, index["status"] == "BUILDING")
                accepted_prefix.append(dict(entry))
            pressure_failures = 0
            if index["status"] == "COMPLETE":
                break
        elif status == 404:
            pressure_failures = 0  # No package state yet; never start a build here.
        elif status in (429, 502, 503, 504):
            pressure_failures += 1
            if pressure_failures >= 2:
                raise ValueError("BUNDLE_INDEX_PRESSURE_CIRCUIT_OPEN")
        else:
            raise ValueError("BUNDLE_INDEX_UNAVAILABLE")
        elapsed = clock() - started
        if elapsed >= 600:
            raise ValueError("BUNDLE_INDEX_PREPARATION_DEADLINE")
        wait = min(delay, 600-elapsed)
        emit({"schema": "fly_bundle_staging_receipt_v1", "status": "INDEX_WAITING",
              "generation": generation, "elapsed_seconds": elapsed,
              "next_retry_seconds": wait, "packages": package_count, "ack_sent": False})
        sleep(wait)
        delay = min(delay * 2, 30)
    if clock() - started >= 1800:
        raise ValueError("BUNDLE_TRANSFER_DEADLINE")
    emit({"schema": "fly_bundle_staging_receipt_v1", "status": "COMPLETE",
          "packages": len(accepted_prefix), "files": len(seen_paths), "ack_sent": False})


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("CLIENT_REQUEST_LIMIT")
        request = json.loads(raw)
        run(request, emit=lambda receipt: print(json.dumps(receipt, separators=(",", ":")), flush=True))
        return 0
    except Exception as error:
        code = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", code):
            code = "BUNDLE_CLIENT_FAILED"
        print(json.dumps({"schema": "fly_bundle_staging_receipt_v1", "status": "FAILED", "error": code}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

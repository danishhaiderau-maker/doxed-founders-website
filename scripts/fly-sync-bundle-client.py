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
from data_sync_bundle_client import fetch_verified_package, BundleClientError, sanitize_diagnostic

MAX_INPUT = 32 * 1024 * 1024
MAX_META = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the admin credential to another URL.


def sanitize_index_diagnostic(value):
    if not isinstance(value, dict) or set(value) != {"generation_id", "phase", "attempts", "http_status", "transport_error"}:
        return None
    if (not isinstance(value["generation_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["generation_id"])
            or value["phase"] != "INDEX" or type(value["attempts"]) is not int or value["attempts"] != 2
            or (value["http_status"] is None) == (value["transport_error"] is None)
            or (value["http_status"] is not None and (type(value["http_status"]) is not int or value["http_status"] not in (429,502,503,504)))
            or value["transport_error"] not in (None, "TIMEOUT", "CONNECTION_ERROR")):
        return None
    return dict(value)


class IndexPressureError(ValueError):
    def __init__(self, diagnostic):
        super().__init__("BUNDLE_INDEX_PRESSURE_CIRCUIT_OPEN")
        self.diagnostic = sanitize_index_diagnostic(diagnostic)


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
            except URLError as error:
                if isinstance(error.reason, TimeoutError):
                    raise TimeoutError("TRANSPORT_TIMEOUT") from None
                raise ConnectionError("TRANSPORT_CONNECTION_ERROR") from None
            with response:
                limit = MAX_META if ("descriptor=1" in relative or "/bundles?" in relative) else 1024 * 1024
                return response.status, dict(response.headers), response.read(limit + 1)
    index_url = "/api/data-sync/bundles?" + urlencode({
        "generation_id": generation["inventory_generation_id"]})
    delay, pressure_failures = 5, 0
    accepted_prefix = []
    seen_paths = set()
    last_progress = started
    def remaining_budget():
        now = clock()
        if now - started >= 1800:
            raise ValueError('BUNDLE_TRANSFER_DEADLINE')
        if now - last_progress >= 600:
            raise ValueError('BUNDLE_INDEX_PREPARATION_DEADLINE')
        return min(1800 - (now-started), 600 - (now-last_progress))
    def consume(entry, building):
        remaining = remaining_budget()
        staged = fetch_verified_package(entry, generation, original, request["staging_root"], fetch,
            deadline_sec=min(120, remaining), clock=clock, sleep=sleep,
            verified_local_root=request.get("verified_local_root"))
        for member in staged["members"]:
            if member["path"] in seen_paths:
                raise ValueError("BUNDLE_MEMBER_REPEATED_ACROSS_PACKAGES")
            seen_paths.add(member["path"])
        emit({"schema":"fly_bundle_staging_receipt_v1", "status":"PACKAGE_VERIFIED",
              "generation":generation, **staged})
        remaining = remaining_budget()
        sleep(min(0.5, remaining))
    while True:
        elapsed = clock() - started
        remaining_budget()
        transport_error = None
        try:
            status, headers, body = fetch(index_url, timeout=min(30, remaining_budget()))
        except (TimeoutError, ConnectionError, URLError) as error:
            reason = error.reason if isinstance(error, URLError) else error
            transport_error = "TIMEOUT" if isinstance(reason, TimeoutError) else "CONNECTION_ERROR"
            status, headers, body = 503, {}, b""
        elapsed = clock() - started
        remaining_budget()
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
                last_progress = clock()
            pressure_failures = 0
            if index["status"] == "COMPLETE":
                break
        elif status == 404:
            pressure_failures = 0  # No package state yet; never start a build here.
        elif status in (429, 502, 503, 504):
            pressure_failures += 1
            if pressure_failures >= 2:
                raise IndexPressureError({"generation_id":generation["inventory_generation_id"], "phase":"INDEX",
                    "attempts":pressure_failures, "http_status":None if transport_error else status,
                    "transport_error":transport_error})
        else:
            raise ValueError("BUNDLE_INDEX_UNAVAILABLE")
        elapsed = clock() - started
        remaining_budget()
        retry_after = next((str(v) for k,v in headers.items() if str(k).lower() == "retry-after"), "") if hasattr(headers, "items") else ""
        advertised = min(int(retry_after), 30) if retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 3 else 0
        wait = min(max(delay, advertised), remaining_budget())
        emit({"schema": "fly_bundle_staging_receipt_v1", "status": "INDEX_WAITING",
              "generation": generation, "elapsed_seconds": elapsed,
              "idle_elapsed_seconds": clock() - last_progress, "verified_packages": len(accepted_prefix),
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
        receipt = {"schema": "fly_bundle_staging_receipt_v1", "status": "FAILED", "error": code}
        diagnostic = sanitize_diagnostic(error.diagnostic) if isinstance(error, BundleClientError) else None
        if diagnostic is not None:
            receipt["diagnostic"] = diagnostic
        index_diagnostic = sanitize_index_diagnostic(error.diagnostic) if isinstance(error, IndexPressureError) else None
        if index_diagnostic is not None:
            receipt["index_diagnostic"] = index_diagnostic
        print(json.dumps(receipt), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded direct-Fly transport adapter; stdout contains verified staging only."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
from data_sync_bundle_client import fetch_verified_package

MAX_INPUT = 32 * 1024 * 1024
MAX_META = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the admin credential to another URL.


def run(request, *, emit, fetch=None, sleep=time.sleep):
    if request.get("source_url", "").rstrip("/") != "https://doxed-btc-bot.fly.dev":
        raise ValueError("NON_CANONICAL_SOURCE")
    manifest = request["manifest"]
    generation = {key: manifest.get(key) for key in (
        "inventory_generation_id", "inventory_sha256", "source_git_rev",
        "collection_epoch_id", "tile_registry_signature", "ack_eligible")}
    if generation["ack_eligible"] is not True:
        raise ValueError("MANIFEST_NOT_ACK_ELIGIBLE")
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
    status, headers, body = fetch("/api/data-sync/bundles?" + urlencode({
        "generation_id": generation["inventory_generation_id"]}), timeout=30)
    if status != 200 or len(body) > MAX_META:
        raise ValueError("BUNDLE_INDEX_UNAVAILABLE")
    index = json.loads(body)
    if (index.get("schema") != "fly_runtime_transport_bundle_index_v1"
            or index.get("status") != "COMPLETE"
            or index.get("ack_authority") != "ORIGINAL_MANIFEST_ROWS_ONLY"
            or any(index.get("generation", {}).get(key) != value
                   for key, value in generation.items() if key != "ack_eligible")):
        raise ValueError("BUNDLE_INDEX_NOT_COMPLETE_OR_MATCHED")
    packages = index.get("packages")
    if not isinstance(packages, list) or len(packages) > 4096:
        raise ValueError("BUNDLE_INDEX_LIMIT")
    seen_packages = set()
    seen_paths = set()
    started = time.monotonic()
    for entry in packages:
        if not isinstance(entry, dict) or entry.get("package_sha256") in seen_packages:
            raise ValueError("BUNDLE_INDEX_DUPLICATE")
        if time.monotonic() - started > 1800:
            raise ValueError("BUNDLE_TRANSFER_DEADLINE")
        seen_packages.add(entry.get("package_sha256"))
        staged = fetch_verified_package(entry, generation, original, request["staging_root"], fetch)
        for member in staged["members"]:
            if member["path"] in seen_paths:
                raise ValueError("BUNDLE_MEMBER_REPEATED_ACROSS_PACKAGES")
            seen_paths.add(member["path"])
        emit({"schema": "fly_bundle_staging_receipt_v1", "status": "PACKAGE_VERIFIED",
              "generation": generation, **staged})
        sleep(0.5)  # Yield once per package, not once per tiny member.
    emit({"schema": "fly_bundle_staging_receipt_v1", "status": "COMPLETE",
          "packages": len(seen_packages), "files": len(seen_paths), "ack_sent": False})


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

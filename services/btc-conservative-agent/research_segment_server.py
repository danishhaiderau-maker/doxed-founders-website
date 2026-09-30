"""Authenticated read-only segment server and laptop ACK recorder (volume sink).

Mounted by bot.py as a thin WSGI dispatcher *in front of* the Flask app, so a
segment request never enters Flask routing, ``before_request`` hooks, the
trade lock or any bot state. It runs on the bounded dashboard server's own
reserved worker class and only reads immutable files written by
``research_segment_shipper`` with ``RESEARCH_SEGMENTS_SINK=volume``.

Routes (all require ``X-Bot-Admin-Token``; fail closed when unset):

    GET  /api/research-segments/<prefix>/head        shipper status + laptop ACK head
    GET  /api/research-segments/<prefix>/man/<seq>   manifest bytes
    GET  /api/research-segments/<prefix>/seg/<seq>   segment bytes (streamed)
    GET  /api/research-segments/<prefix>/ack/<seq>   recorded laptop ACK
    POST /api/research-segments/<prefix>/ack         laptop ACK "through seq N"

Files are immutable, so nothing is rehashed per request: the segment ETag is
the ``segment_sha256`` declared by its manifest and the manifest ETag is its
own sha256 (computed once, cached). A segment is published only once its
manifest exists. ACKs are write-once and monotonic, and must name the exact
manifest-chain head hash at ``through_seq``.

This module never deletes anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path

import research_segment_format as fmt
from research_segment_store import PreconditionFailed, VolumeStore, volume_store_root

ROUTE_PREFIX = "/api/research-segments/"
RECEIPT_SCHEMA = "research_segment_ack_receipt_v1"
HEAD_SCHEMA = "research_segment_head_v1"
CHUNK_BYTES = 64 * 1024
MAX_ACK_BODY_BYTES = 4096
MANIFEST_CACHE_LIMIT = 4096
_ROUTE_RE = re.compile(r"^/api/research-segments/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/"
                       r"(head|ack|man/\d{1,12}|seg/\d{1,12}|ack/\d{1,12})$")


def receipt_key(prefix: str, seq: int) -> str:
    return f"{fmt.validate_prefix(prefix)}/acks/laptop-receipts/{fmt.seq_token(seq)}.json"


class _FileStream:
    """Bounded-chunk iterator; the WSGI server closes it when done."""

    def __init__(self, handle, remaining: int):
        self._handle = handle
        self._remaining = remaining

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self._remaining <= 0:
            raise StopIteration
        chunk = self._handle.read(min(CHUNK_BYTES, self._remaining))
        if not chunk:
            raise StopIteration
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        self._handle.close()


class SegmentServer:
    def __init__(self, *, store_root: Path, state_dir: Path, admin_token: str,
                 prefix: str = "v1", enabled: bool = True, clock=time.time):
        self.store_root = Path(store_root)
        self.state_dir = Path(state_dir)
        self.prefix = fmt.validate_prefix(prefix)
        self.enabled = bool(enabled)
        self.clock = clock
        self._token = (admin_token or "").strip().encode("utf-8")
        self._manifest_cache: dict[int, dict] = {}
        self._cache_lock = threading.Lock()
        self._ack_lock = threading.Lock()
        self._acked: dict | None = None
        self._store: VolumeStore | None = None

    # ------------------------------------------------------------- helpers
    @property
    def store(self) -> VolumeStore:
        if self._store is None:
            self._store = VolumeStore(self.store_root)
        return self._store

    def _file(self, key: str) -> Path:
        return self.store_root.joinpath(*key.split("/"))

    def _authorized(self, environ) -> bool:
        presented = (environ.get("HTTP_X_BOT_ADMIN_TOKEN") or "").strip().encode("utf-8")
        return bool(presented) and hmac.compare_digest(presented, self._token)

    @staticmethod
    def _respond(start_response, status: str, payload: dict, headers=None):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        start_response(status, [("Content-Type", "application/json"),
                                ("Content-Length", str(len(body))),
                                ("Cache-Control", "no-store"), *(headers or [])])
        return [body]

    def manifest_info(self, seq: int) -> dict | None:
        """Return cached {sha256, segment_sha256, segment_size, size} for a published seq."""
        with self._cache_lock:
            cached = self._manifest_cache.get(seq)
        if cached is not None:
            return cached
        path = self._file(fmt.manifest_key(self.prefix, seq))
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        manifest = fmt.parse_manifest(raw, prefix=self.prefix, expected_seq=seq)
        info = {"sha256": fmt.sha256_bytes(raw), "size": len(raw),
                "segment_sha256": manifest["segment_sha256"],
                "segment_size": manifest["segment_size"]}
        with self._cache_lock:
            if len(self._manifest_cache) >= MANIFEST_CACHE_LIMIT:
                self._manifest_cache.clear()
            self._manifest_cache[seq] = info
        return info

    def _read_json(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def acked_head(self) -> dict:
        """Highest recorded ACK. The ACK directory is listed once per process."""
        with self._ack_lock:
            if self._acked is None:
                best = {"through_seq": 0, "manifest_sha256": None, "received_at": None}
                for key in self.store.list_keys(fmt.ack_prefix(self.prefix)):
                    token = key.rsplit("/", 1)[-1].removesuffix(".json")
                    if token.isdigit() and int(token) > best["through_seq"]:
                        ack = fmt.parse_ack(self.store.get(key) or b"")
                        receipt = self._read_json(self._file(receipt_key(self.prefix, int(token))))
                        best = {"through_seq": int(token), "manifest_sha256": ack["manifest_sha256"],
                                "received_at": receipt.get("received_at")}
                self._acked = best
            return dict(self._acked)

    # --------------------------------------------------------------- routes
    def _head(self, start_response):
        status = self._read_json(self.state_dir / "status.json")
        payload = {
            "schema": HEAD_SCHEMA, "prefix": self.prefix, "sink": "volume",
            "published_seq": int(status.get("shipped_seq") or 0),
            "last_manifest_sha256": status.get("last_manifest_sha256"),
            "store_bytes": status.get("store_bytes"),
            "max_store_bytes": status.get("max_store_bytes"),
            "unshipped_bytes": status.get("unshipped_bytes"),
            "shipper_last_error": status.get("last_error"),
            "shipper_updated_at": status.get("updated_at"),
            "shipper_last_segment_at": status.get("last_segment_at"),
            "oversized_paths": status.get("oversized_paths"),
            "throttled_snapshots": status.get("throttled_snapshots"),
            "laptop_acked": self.acked_head(),
            "pruning_enabled": False,
        }
        return self._respond(start_response, "200 OK", payload)

    def _serve_manifest(self, environ, start_response, seq: int):
        info = self.manifest_info(seq)
        if info is None:
            return self._respond(start_response, "404 Not Found", {"error": "NOT_PUBLISHED", "seq": seq})
        etag = f'"{info["sha256"]}"'
        if environ.get("HTTP_IF_NONE_MATCH") == etag:
            start_response("304 Not Modified", [("ETag", etag)])
            return [b""]
        raw = self._file(fmt.manifest_key(self.prefix, seq)).read_bytes()
        start_response("200 OK", [("Content-Type", "application/json"),
                                  ("Content-Length", str(len(raw))), ("ETag", etag),
                                  ("Cache-Control", "private, max-age=31536000, immutable")])
        return [raw]

    def _serve_segment(self, environ, start_response, seq: int):
        info = self.manifest_info(seq)
        if info is None:
            return self._respond(start_response, "404 Not Found", {"error": "NOT_PUBLISHED", "seq": seq})
        etag = f'"{info["segment_sha256"]}"'
        if environ.get("HTTP_IF_NONE_MATCH") == etag:
            start_response("304 Not Modified", [("ETag", etag)])
            return [b""]
        try:
            handle = self._file(fmt.segment_key(self.prefix, seq)).open("rb")
        except FileNotFoundError:
            return self._respond(start_response, "500 Internal Server Error",
                                 {"error": "SEGMENT_MISSING_FOR_PUBLISHED_MANIFEST", "seq": seq})
        size = os.fstat(handle.fileno()).st_size
        if size != info["segment_size"]:
            handle.close()
            return self._respond(start_response, "500 Internal Server Error",
                                 {"error": "SEGMENT_SIZE_MISMATCH", "seq": seq})
        start_response("200 OK", [("Content-Type", "application/gzip"),
                                  ("Content-Length", str(size)), ("ETag", etag),
                                  ("Cache-Control", "private, max-age=31536000, immutable")])
        return _FileStream(handle, size)

    def _serve_ack(self, start_response, seq: int):
        raw = self.store.get(fmt.ack_key(self.prefix, seq))
        if raw is None:
            return self._respond(start_response, "404 Not Found", {"error": "NO_ACK", "seq": seq})
        start_response("200 OK", [("Content-Type", "application/json"),
                                  ("Content-Length", str(len(raw))), ("Cache-Control", "no-store")])
        return [raw]

    def record_ack(self, raw: bytes) -> tuple[str, dict]:
        """Validate and record one cumulative ACK. Returns (http_status, payload)."""
        try:
            ack = fmt.parse_ack(raw)
        except fmt.SegmentFormatError as exc:
            return "400 Bad Request", {"error": "INVALID_ACK", "detail": str(exc)}
        if fmt.canonical_json(ack) != raw:
            return "400 Bad Request", {"error": "ACK_NOT_CANONICAL"}
        through, head = int(ack["through_seq"]), ack["manifest_sha256"]
        info = self.manifest_info(through)
        if info is None:
            return "409 Conflict", {"error": "ACK_AHEAD_OF_PUBLISHED", "through_seq": through}
        if not hmac.compare_digest(info["sha256"], head):
            return "409 Conflict", {"error": "ACK_HEAD_MISMATCH", "through_seq": through}
        current = self.acked_head()
        with self._ack_lock:
            current = dict(self._acked or current)
            if through < current["through_seq"]:
                return "409 Conflict", {"error": "ACK_REGRESSION", "through_seq": through,
                                        "acked_seq": current["through_seq"]}
            if through == current["through_seq"]:
                if current["manifest_sha256"] == head:
                    return "200 OK", {"ok": True, "result": "ALREADY_RECORDED", **current}
                return "409 Conflict", {"error": "ACK_HEAD_MISMATCH", "through_seq": through}
            key = fmt.ack_key(self.prefix, through)
            try:
                self.store.put_if_absent(key, raw, sha256=fmt.sha256_bytes(raw),
                                         content_type="application/json")
            except PreconditionFailed:
                existing = fmt.parse_ack(self.store.get(key) or b"")
                if existing["manifest_sha256"] != head:
                    return "409 Conflict", {"error": "ACK_HEAD_MISMATCH", "through_seq": through}
            received_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))
            receipt = fmt.canonical_json({
                "schema": RECEIPT_SCHEMA, "through_seq": through, "manifest_sha256": head,
                "received_at": received_at, "applied_at": ack.get("applied_at"),
                "verifier_version": ack.get("verifier_version"),
            })
            try:
                self.store.put_if_absent(receipt_key(self.prefix, through), receipt,
                                         sha256=fmt.sha256_bytes(receipt),
                                         content_type="application/json")
            except PreconditionFailed:
                received_at = self._read_json(
                    self._file(receipt_key(self.prefix, through))).get("received_at", received_at)
            self._acked = {"through_seq": through, "manifest_sha256": head,
                           "received_at": received_at}
            return "201 Created", {"ok": True, "result": "RECORDED", **self._acked}

    def _post_ack(self, environ, start_response):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_ACK_BODY_BYTES:
            return self._respond(start_response, "413 Payload Too Large"
                                 if length > MAX_ACK_BODY_BYTES else "400 Bad Request",
                                 {"error": "ACK_BODY_SIZE"})
        raw = environ["wsgi.input"].read(length)
        status, payload = self.record_ack(raw)
        return self._respond(start_response, status, payload)

    # ------------------------------------------------------------------ WSGI
    def __call__(self, environ, start_response):
        if not self._token:
            return self._respond(start_response, "503 Service Unavailable",
                                 {"error": "ADMIN_TOKEN_NOT_CONFIGURED"})
        if not self._authorized(environ):
            return self._respond(start_response, "401 Unauthorized", {"error": "unauthorized"})
        if not self.enabled:
            return self._respond(start_response, "404 Not Found", {"error": "SINK_NOT_VOLUME"})
        match = _ROUTE_RE.match(environ.get("PATH_INFO") or "")
        if not match or match.group(1) != self.prefix:
            return self._respond(start_response, "404 Not Found", {"error": "UNKNOWN_ROUTE"})
        route, method = match.group(2), (environ.get("REQUEST_METHOD") or "GET").upper()
        try:
            if route == "ack":
                if method != "POST":
                    return self._respond(start_response, "405 Method Not Allowed", {"error": "POST only"})
                return self._post_ack(environ, start_response)
            if method != "GET":
                return self._respond(start_response, "405 Method Not Allowed", {"error": "GET only"})
            if route == "head":
                return self._head(start_response)
            kind, _, token = route.partition("/")
            seq = int(token)
            if seq < 1:
                return self._respond(start_response, "404 Not Found", {"error": "INVALID_SEQ"})
            if kind == "man":
                return self._serve_manifest(environ, start_response, seq)
            if kind == "seg":
                return self._serve_segment(environ, start_response, seq)
            return self._serve_ack(start_response, seq)
        except fmt.SegmentFormatError as exc:
            return self._respond(start_response, "500 Internal Server Error",
                                 {"error": "STORE_CONTRACT_VIOLATION", "detail": str(exc)})


def server_from_env(environ=None) -> SegmentServer:
    env = os.environ if environ is None else environ
    volume = Path(env.get("BOT_DATA_DIR") or "/app/data")
    return SegmentServer(
        store_root=volume_store_root(env),
        state_dir=Path(env.get("RESEARCH_SEGMENTS_STATE_DIR") or volume / "segment-shipper"),
        admin_token=env.get("BOT_ADMIN_TOKEN") or "",
        prefix=(env.get("RESEARCH_SEGMENTS_PREFIX") or "v1").strip(),
        enabled=(env.get("RESEARCH_SEGMENTS_SINK") or "").strip().lower() == "volume",
    )


def mount(app, environ=None):
    """Wrap a WSGI app so only ``/api/research-segments/*`` is served here."""
    server = server_from_env(environ)

    def dispatch(wsgi_environ, start_response):
        if (wsgi_environ.get("PATH_INFO") or "").startswith(ROUTE_PREFIX):
            return server(wsgi_environ, start_response)
        return app(wsgi_environ, start_response)

    dispatch.research_segment_server = server
    return dispatch

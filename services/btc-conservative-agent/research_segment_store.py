"""Write-once object stores for research segments (stdlib only).

``S3Store`` speaks the S3 REST API with AWS Signature V4 so it works against
Tigris (and any S3-compatible store) without boto3 on the Fly image or the
laptop. ``LocalDirectoryStore`` offers the same write-once semantics on a
local directory for tests and offline dry runs.

Neither store exposes delete or unconditional overwrite: every write is a
create-if-absent (``If-None-Match: *``). A conflicting write raises
``PreconditionFailed`` and the caller decides whether the existing object is
the same bytes (idempotent retry) or a fail-closed conflict.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

META_SHA256 = "x-amz-meta-sha256"
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class StoreError(RuntimeError):
    """Transport or protocol failure talking to the object store."""


class PreconditionFailed(StoreError):
    """The key already exists; the write-once contract refused the overwrite."""


class ObjectStore:
    def put_if_absent(self, key: str, body: bytes, *, sha256: str, content_type: str) -> None:
        raise NotImplementedError

    def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    def head_sha256(self, key: str) -> str | None:
        raise NotImplementedError

    def list_keys(self, prefix: str, start_after: str = "") -> list[str]:
        raise NotImplementedError


class LocalDirectoryStore(ObjectStore):
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/") or "\\" in key:
            raise StoreError(f"unsafe key {key!r}")
        return self.root / key

    def put_if_absent(self, key: str, body: bytes, *, sha256: str, content_type: str) -> None:
        if hashlib.sha256(body).hexdigest() != sha256:
            raise StoreError("declared sha256 does not match body")
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        temporary.write_bytes(body)
        try:
            # os.link is atomic create-if-absent on POSIX and NTFS.
            os.link(temporary, target)
        except FileExistsError as exc:
            raise PreconditionFailed(key) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str) -> bytes | None:
        target = self._path(key)
        return target.read_bytes() if target.is_file() else None

    def head_sha256(self, key: str) -> str | None:
        raw = self.get(key)
        return None if raw is None else hashlib.sha256(raw).hexdigest()

    def list_keys(self, prefix: str, start_after: str = "") -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.is_dir():
            return []
        keys = []
        for path in base.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix) and key > start_after:
                    keys.append(key)
        return sorted(keys)


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return urllib.parse.quote(value, safe="/-_.~" if keep_slash else "-_.~")


class S3Store(ObjectStore):
    """Minimal path-style S3 client with SigV4 and write-once PUTs."""

    def __init__(
        self, *, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str,
        region: str = "auto", timeout: float = 60.0, attempts: int = 4,
    ):
        parsed = urllib.parse.urlsplit(endpoint)
        loopback = parsed.hostname in ("127.0.0.1", "localhost")
        if not parsed.netloc or not (parsed.scheme == "https" or (loopback and parsed.scheme == "http")):
            raise StoreError("object store endpoint must be an https URL")
        if not bucket or not access_key_id or not secret_access_key:
            raise StoreError("object store bucket and credentials are required")
        self._endpoint = f"{parsed.scheme}://{parsed.netloc}"
        self._host = parsed.netloc
        self._bucket = bucket
        self._access_key_id = access_key_id
        self._secret = secret_access_key
        self._region = region or "auto"
        self._timeout = float(timeout)
        self._attempts = max(1, int(attempts))

    def __repr__(self) -> str:
        return f"S3Store(endpoint={self._endpoint!r}, bucket={self._bucket!r})"

    def _headers(self, method: str, uri: str, query: str, payload_hash: str, extra: dict) -> dict:
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        headers = {
            "host": self._host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
        }
        headers.update({name.lower(): str(value).strip() for name, value in extra.items()})
        signed = sorted(headers)
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed)
        signed_headers = ";".join(signed)
        canonical_request = "\n".join(
            [method, uri, query, canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        signing_key = _sign(_sign(_sign(_sign(
            ("AWS4" + self._secret).encode("utf-8"), date), self._region), "s3"), "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers.pop("host")
        return headers

    def _request(self, method: str, key: str, *, body: bytes = b"", query: dict | None = None,
                 extra: dict | None = None):
        uri = "/" + _uri_encode(self._bucket, keep_slash=False)
        if key:
            uri += "/" + _uri_encode(key, keep_slash=True)
        query_string = "&".join(
            f"{_uri_encode(k, keep_slash=False)}={_uri_encode(v, keep_slash=False)}"
            for k, v in sorted((query or {}).items())
        )
        payload_hash = hashlib.sha256(body).hexdigest()
        last_error = None
        for attempt in range(self._attempts):
            headers = self._headers(method, uri, query_string, payload_hash, extra or {})
            url = self._endpoint + uri + (f"?{query_string}" if query_string else "")
            request = urllib.request.Request(
                url, data=body if method == "PUT" else None, method=method, headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return response.status, dict(response.headers.items()), response.read()
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                if status == 412:
                    raise PreconditionFailed(key) from None
                if status == 404:
                    return 404, {}, b""
                # 409 is S3's concurrent-conditional-write conflict: retry and
                # let the next attempt resolve to 200 or 412.
                if status not in (409, 429) and status < 500:
                    raise StoreError(f"{method} {key or '/'} failed with HTTP {status}") from None
                last_error = f"HTTP {status}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = type(exc).__name__
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise StoreError(f"{method} {key or '/'} failed after retries: {last_error}")

    def put_if_absent(self, key: str, body: bytes, *, sha256: str, content_type: str) -> None:
        if hashlib.sha256(body).hexdigest() != sha256:
            raise StoreError("declared sha256 does not match body")
        self._request("PUT", key, body=body, extra={
            "If-None-Match": "*", "Content-Type": content_type,
            "Content-Length": str(len(body)), META_SHA256: sha256,
        })

    def get(self, key: str) -> bytes | None:
        status, _headers, body = self._request("GET", key)
        return None if status == 404 else body

    def head_sha256(self, key: str) -> str | None:
        status, headers, _body = self._request("HEAD", key)
        if status == 404:
            return None
        lowered = {name.lower(): value for name, value in headers.items()}
        return lowered.get(META_SHA256)

    def list_keys(self, prefix: str, start_after: str = "") -> list[str]:
        keys, token = [], ""
        while True:
            query = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                query["continuation-token"] = token
            elif start_after:
                query["start-after"] = start_after
            _status, _headers, body = self._request("GET", "", query=query)
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise StoreError("ListObjectsV2 returned invalid XML") from exc
            keys.extend(node.text for node in root.iter(f"{_S3_NS}Key") if node.text)
            truncated = (root.findtext(f"{_S3_NS}IsTruncated") or "").lower() == "true"
            token = root.findtext(f"{_S3_NS}NextContinuationToken") or ""
            if not truncated or not token:
                return sorted(keys)


def store_from_env(environ=None, *, prefix: str = "RESEARCH_SEGMENTS_") -> ObjectStore:
    """Build a store from environment without ever echoing credentials.

    ``<prefix>LOCAL_STORE_DIR`` selects a local directory store (tests, dry
    runs). Otherwise S3 credentials are required.
    """
    env = os.environ if environ is None else environ
    local = (env.get(f"{prefix}LOCAL_STORE_DIR") or "").strip()
    if local:
        return LocalDirectoryStore(local)
    return S3Store(
        endpoint=(env.get(f"{prefix}ENDPOINT") or "https://fly.storage.tigris.dev").strip(),
        bucket=(env.get(f"{prefix}BUCKET") or "").strip(),
        access_key_id=(env.get(f"{prefix}ACCESS_KEY_ID") or "").strip(),
        secret_access_key=(env.get(f"{prefix}SECRET_ACCESS_KEY") or "").strip(),
        region=(env.get(f"{prefix}REGION") or "auto").strip(),
    )

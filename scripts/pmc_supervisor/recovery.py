"""Read-only verifier of separately authorized OFFLINE human recovery receipts.

RSASSA-PKCS1-v1_5 SHA256 verification, fixed exponent and 2048..4096-bit keys.
No signing functionality, key discovery, auto-clear, or CLI recovery shortcut.
"""
import hashlib
import json
import re
from .policy import Hold, ROLES, ROOT, DATA, REPORTS, SHA, file_hash, no_reparse, packed

PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

def verify_signature(body, signature, public):
    try:
        if set(public) != {"n", "e"} or public["e"] != 65537:
            return False
        n = int(public["n"], 16)
        size = (n.bit_length() + 7) // 8
        sig = bytes.fromhex(signature)
        if not 2048 <= n.bit_length() <= 4096 or len(sig) != size or int.from_bytes(sig, "big") >= n:
            return False
        decoded = pow(int.from_bytes(sig, "big"), 65537, n).to_bytes(size, "big")
        tail = PREFIX + hashlib.sha256(packed(body)).digest()
        expected = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
        return decoded == expected
    except (ValueError, TypeError, KeyError):
        return False

def read_receipt(root, pin, key_pin):
    path = no_reparse(root / "human-clear.json")
    key_path = no_reparse(root / "human-clear-public-key.json")
    if path.stat().st_size > 8192 or key_path.stat().st_size > 4096 or file_hash(path) != pin:
        raise Hold("IDENTITY_MISMATCH")
    # Public key is itself pinned in the signed installation's owners receipt;
    # no dynamically supplied key is accepted. Verify an extra permanent pin.
    if not SHA.fullmatch(key_pin) or file_hash(key_path) != key_pin:
        raise Hold("IDENTITY_MISMATCH")
    row, key = json.loads(path.read_bytes()), json.loads(key_path.read_bytes())
    if set(row) != {"body", "signature"}:
        raise Hold("IDENTITY_MISMATCH")
    body = row["body"]
    keys = {"who", "why", "remediation", "role", "latch_id", "previous_hash", "new_config_hash", "new_code_hash", "roots"}
    if set(body) != keys or body["role"] not in ROLES or body["roots"] != [ROOT, DATA, REPORTS]:
        raise Hold("IDENTITY_MISMATCH")
    for name in ("who", "why", "remediation"):
        if not isinstance(body[name], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._-]{3,159}", body[name]):
            raise Hold("IDENTITY_MISMATCH")
    for name in ("previous_hash", "new_config_hash", "new_code_hash"):
        if not SHA.fullmatch(body[name]):
            raise Hold("IDENTITY_MISMATCH")
    if not verify_signature(body, row["signature"], key):
        raise Hold("IDENTITY_MISMATCH")
    return row

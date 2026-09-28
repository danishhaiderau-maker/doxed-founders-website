"""Print the Python-canonical SHA-256 of a lifecycle manifest payload."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manifest.pop("manifest_sha256", None)
payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())

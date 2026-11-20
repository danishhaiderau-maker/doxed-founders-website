"""CI-only, offline diagnosis; never modifies the producer or production roots.

Prints only synthetic byte counts/booleans and the CRT mode from a descriptor
opened by the EXISTING collector SegmentFile implementation. Not a PASS waiver.
"""
import json
import os
import sys
import tempfile
from collections import deque
from pathlib import Path


def main():
    if os.name != "nt":
        raise SystemExit("Windows CI diagnostic only")
    import msvcrt
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "services" / "public-market-collector"))
    from public_market_collector.framing import SegmentFile
    with tempfile.TemporaryDirectory(prefix="pmc-supervisor-keyless-io-") as directory:
        path = Path(directory) / "synthetic.bin"
        segment = SegmentFile(path, deque(maxlen=8))
        blob = bytes(range(256))  # guaranteed LF, no market payload or credentials
        try:
            written = os.write(segment.fd, blob)
            os.fsync(segment.fd)
            original_mode = msvcrt.setmode(segment.fd, os.O_BINARY)
        finally:
            segment.mark_closed()
        actual = path.read_bytes()
        print(json.dumps({"diagnostic": "EXISTING_COLLECTOR_SEGMENT_FD", "input_bytes": len(blob),
                          "reported_write_bytes": written, "stored_bytes": len(actual),
                          "round_trip_equal": actual == blob, "original_crt_mode": original_mode,
                          "binary_mode": os.O_BINARY, "text_mode": os.O_TEXT}, sort_keys=True))
        return 0 if actual == blob else 1


if __name__ == "__main__":
    raise SystemExit(main())

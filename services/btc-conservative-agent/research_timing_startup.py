"""Prepare a forward-only timing declaration before runtime workers start.

The caller supplies trusted live identity and startup time and installs the
returned pin pair before starting workers. This module changes no environment,
orders, historical rows, or epochs and never supplies missing model defaults.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from research_timing_capture import load_runtime_timing_config, materialize_timing_declarations


def _directory(raw):
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("TIMING_STARTUP_ABSOLUTE_DIRECTORY_REQUIRED")
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("TIMING_STARTUP_DIRECTORY_UNTRUSTED")
    if path.resolve(strict=True) != path:
        raise ValueError("TIMING_STARTUP_DIRECTORY_UNTRUSTED")
    return path


def prepare_startup_timing_declaration(*, environ, source_revision, epoch_id,
                                       tile_config_signature, output_directory, startup_time):
    """Return replacement pins only after immutable publication succeeds.

    Only a trusted startup owner may call this; it must obtain identity from
    the current runtime, not from a request or the old declaration. Time must
    be sampled at this startup boundary, never copied from an earlier signal.
    The directory must already be provisioned and not writable by untrusted
    concurrent actors. It is not created by this helper.
    """
    if (not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
            or not isinstance(epoch_id, str) or not re.fullmatch(r"epoch-[A-Za-z0-9_-]{1,128}", epoch_id)
            or not isinstance(tile_config_signature, str) or not re.fullmatch(r"[0-9a-f]{64}", tile_config_signature)
            or type(startup_time) not in (int, float) or not math.isfinite(startup_time) or startup_time <= 0):
        raise ValueError("TIMING_STARTUP_IDENTITY_OR_TIME_INVALID")
    loaded = load_runtime_timing_config(environ)
    old = loaded.get("research_timing_config")
    if not isinstance(old, dict):
        raise ValueError("TIMING_STARTUP_PINNED_CONFIG_UNAVAILABLE")
    # Exercise the existing complete schema, model, hash, and causal checks.
    old_row = {key: old.get(key) for key in ("epoch_id", "source_revision", "tile_config_signature")}
    old_row.update(loaded, signal_ts=startup_time)
    captures = {"directional_schedules": {side: {"direction": side, "capture_signature": side}
                                         for side in ("LONG", "SHORT")}}
    if materialize_timing_declarations(old_row, captures)["status"] != "DECLARED":
        raise ValueError("TIMING_STARTUP_CONFIG_INVALID_OR_FUTURE")
    config = {**old, "source_revision": source_revision, "epoch_id": epoch_id,
              "tile_config_signature": tile_config_signature, "activated_at_ts": startup_time}
    raw = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    directory = _directory(output_directory)
    before = directory.stat()
    destination = directory / ("research-timing-" + digest + ".json")
    temporary = directory / (".research-timing-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _directory(directory)
        after = directory.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("TIMING_STARTUP_DIRECTORY_CHANGED")
        # Hard-link publication is atomic and fails if destination exists;
        # unlike replace it cannot overwrite a prior declaration or link.
        os.link(temporary, destination)
    except Exception as exc:
        raise ValueError("TIMING_STARTUP_PUBLICATION_FAILED") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"BTC_RESEARCH_TIMING_CONFIG_FILE": str(destination),
            "BTC_RESEARCH_TIMING_CONFIG_SHA256": digest}

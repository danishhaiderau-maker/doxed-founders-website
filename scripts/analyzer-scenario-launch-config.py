"""Nonsecret launcher input validation only; never imports or starts analyzer."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat


def _read(path, maximum):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("ANALYZER_SCENARIO_ABSOLUTE_PATH_REQUIRED")
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("ANALYZER_SCENARIO_LINK_REFUSED")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        raise ValueError("ANALYZER_SCENARIO_SIZE_INVALID")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    after = path.stat()
    if (len(raw) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError("ANALYZER_SCENARIO_CHANGED_DURING_READ")
    return raw


def _decode(raw):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise ValueError("ANALYZER_SCENARIO_DUPLICATE_KEY")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("ANALYZER_SCENARIO_NONFINITE")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError("ANALYZER_SCENARIO_OBJECT_REQUIRED")
    return result


def validate(*, config_path="", model_file="", model_sha256=""):
    config_sha = None
    if config_path:
        raw = _read(config_path, 65536)
        config_sha = hashlib.sha256(raw).hexdigest()
        config = _decode(raw)
        if (set(config) != {"schema", "model_file", "model_sha256"}
                or config.get("schema") != "analyzer_shadow_scenario_launch_v1"):
            raise ValueError("ANALYZER_SCENARIO_LAUNCH_SCHEMA_INVALID")
        configured_file, configured_sha = config["model_file"], config["model_sha256"]
        if (not isinstance(configured_file, str) or not isinstance(configured_sha, str)
                or not configured_file or not configured_sha):
            raise ValueError("ANALYZER_SCENARIO_PIN_PAIR_REQUIRED")
        if (model_file or model_sha256) and (model_file, model_sha256) != (configured_file, configured_sha):
            raise ValueError("ANALYZER_SCENARIO_AMBIGUOUS_INPUT")
        model_file, model_sha256 = configured_file, configured_sha
    if not model_file and not model_sha256:
        return {"enabled": False, "config_path": config_path, "config_sha256": config_sha,
                "model_file": "", "model_sha256": ""}
    if not model_file or not re.fullmatch(r"[0-9a-f]{64}", model_sha256):
        raise ValueError("ANALYZER_SCENARIO_PIN_PAIR_REQUIRED")
    raw = _read(model_file, 2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != model_sha256:
        raise ValueError("ANALYZER_SCENARIO_HASH_MISMATCH")
    model = _decode(raw)
    if model.get("schema") not in {"declared_shadow_model_v1", "declared_shadow_scenario_input_v1"}:
        raise ValueError("ANALYZER_SCENARIO_MODEL_SCHEMA_INVALID")
    return {"enabled": True, "config_path": config_path, "config_sha256": config_sha,
            "model_file": model_file, "model_sha256": model_sha256}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", default="")
    parser.add_argument("--model-file", default="")
    parser.add_argument("--model-sha256", default="")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(**vars(args)), sort_keys=True))
    except (ValueError, OSError, TypeError) as error:
        code = str(error)
        if not re.fullmatch(r"ANALYZER_SCENARIO_[A-Z_]+", code):
            code = "ANALYZER_SCENARIO_INPUT_UNAVAILABLE_OR_INVALID"
        print(json.dumps({"error": code}))
        raise SystemExit(2)

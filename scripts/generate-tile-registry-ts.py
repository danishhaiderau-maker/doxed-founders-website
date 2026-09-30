#!/usr/bin/env python3
"""Generate the TypeScript view of the canonical tile registry.

`services/btc-conservative-agent/combo_pathway_config.py` is the sole active
tile registry. TypeScript consumers (relay mirror allowlist, observatory, demo
smoke) import `packages/utils/src/generated/tile-registry.generated.ts` instead
of keeping their own lane or trade-id prefix lists.

Usage:
    python scripts/generate-tile-registry-ts.py          # rewrite the file
    python scripts/generate-tile-registry-ts.py --check  # exit 1 on drift
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "services" / "btc-conservative-agent"
OUTPUT = ROOT / "packages" / "utils" / "src" / "generated" / "tile-registry.generated.ts"


def _load_registry():
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    import combo_pathway_config as registry

    return registry


def render() -> str:
    registry = _load_registry()
    defects = registry.validate_tile_registry()
    if defects:
        raise SystemExit("tile registry invalid: " + ", ".join(defects))
    lanes = [str(lane) for lane in registry.ACTIVE_TILE_ORDER]
    specs = registry.ACTIVE_TILE_REGISTRY
    prefixes = [str(specs[lane]["id_prefix"]) for lane in lanes]
    relay_lanes = [lane for lane in lanes if specs[lane].get("platform_relay_eligible") is True]
    relay_prefixes = [str(specs[lane]["id_prefix"]) for lane in relay_lanes]
    partial_lanes = [lane for lane in lanes if registry.tile_has_partial_exits(specs[lane])]
    partial_prefixes = [str(specs[lane]["id_prefix"]) for lane in partial_lanes]
    retired = sorted(str(lane) for lane in registry.RETIRED_TILE_LANES)

    def arr(values):
        return json.dumps(values, ensure_ascii=True)

    return (
        "// GENERATED FILE - DO NOT EDIT.\n"
        "// Source: services/btc-conservative-agent/combo_pathway_config.py\n"
        "// Regenerate: python scripts/generate-tile-registry-ts.py\n"
        "\n"
        f"export const ACTIVE_TILE_LANES: readonly string[] = {arr(lanes)};\n"
        "\n"
        f"export const ACTIVE_TILE_ID_PREFIXES: readonly string[] = {arr(prefixes)};\n"
        "\n"
        "/** Lanes and trade-id prefixes of tiles the registry marks platform_relay_eligible. */\n"
        f"export const RELAY_ELIGIBLE_TILE_LANES: readonly string[] = {arr(relay_lanes)};\n"
        "\n"
        f"export const RELAY_ELIGIBLE_TILE_ID_PREFIXES: readonly string[] = {arr(relay_prefixes)};\n"
        "\n"
        "/** Tiles whose exit policy reduces a position in parts; relay refuses them until exchange-side reductions are proven. */\n"
        f"export const PARTIAL_EXIT_TILE_LANES: readonly string[] = {arr(partial_lanes)};\n"
        "\n"
        f"export const PARTIAL_EXIT_TILE_ID_PREFIXES: readonly string[] = {arr(partial_prefixes)};\n"
        "\n"
        f"export const RETIRED_TILE_LANES: readonly string[] = {arr(retired)};\n"
        "\n"
        f"export const COMPARISON_BENCHMARK_LANE = {json.dumps(registry.COMPARISON_BENCHMARK_LANE)};\n"
    )


def main(argv: list[str]) -> int:
    content = render()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current.replace("\r\n", "\n") != content:
            print(f"DRIFT: {OUTPUT.relative_to(ROOT)} is stale; run python scripts/generate-tile-registry-ts.py")
            return 1
        print("tile registry TS manifest up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

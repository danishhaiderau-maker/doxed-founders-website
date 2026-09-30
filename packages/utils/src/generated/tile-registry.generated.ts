// GENERATED FILE - DO NOT EDIT.
// Source: services/btc-conservative-agent/combo_pathway_config.py
// Regenerate: python scripts/generate-tile-registry-ts.py

export const ACTIVE_TILE_LANES: readonly string[] = ["FAMILY_CHANDELIER_3", "FAMILY_ATR_TARGET_2_5", "FAMILY_ATR_TRAIL", "FAMILY_HYBRID_RUNNER", "FAMILY_MFE_GIVEBACK"];

export const ACTIVE_TILE_ID_PREFIXES: readonly string[] = ["fc3", "fat", "ftr", "fhy", "fmg"];

/** Lanes and trade-id prefixes of tiles the registry marks platform_relay_eligible. */
export const RELAY_ELIGIBLE_TILE_LANES: readonly string[] = [];

export const RELAY_ELIGIBLE_TILE_ID_PREFIXES: readonly string[] = [];

/** Tiles whose exit policy reduces a position in parts; relay refuses them until exchange-side reductions are proven. */
export const PARTIAL_EXIT_TILE_LANES: readonly string[] = ["FAMILY_HYBRID_RUNNER"];

export const PARTIAL_EXIT_TILE_ID_PREFIXES: readonly string[] = ["fhy"];

export const RETIRED_TILE_LANES: readonly string[] = ["OFFSET_029_ATR_PROTECTED", "OFFSET_029_ATR_REGIME", "OFFSET_029_ATR_TP_25", "PROTECTED_W234_SCENARIO_C"];

export const COMPARISON_BENCHMARK_LANE = "CONTINUOUS";

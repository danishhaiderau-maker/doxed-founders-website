// GENERATED FILE - DO NOT EDIT.
// Source: services/btc-conservative-agent/combo_pathway_config.py
// Regenerate: python scripts/generate-tile-registry-ts.py

export const ACTIVE_TILE_LANES: readonly string[] = ["FAMILY_DANISH_CF", "FAMILY_DANISH_CF_NOES", "FAMILY_DANISH_CF_ALL_SESSIONS", "FAMILY_CONTINUOUS_AUG_ORIGINAL", "FAMILY_COMMITTED_FADE_MAKER_90", "FAMILY_COMMITTED_FADE_TAKER_90", "FAMILY_NOTRADE_FOLLOW_MAKER_60", "FAMILY_XVENUE_SESSION_FOLLOW_60M"];

export const ACTIVE_TILE_ID_PREFIXES: readonly string[] = ["dcf", "dcn", "dca", "caug", "cfm", "cft", "ntf", "xvs"];

/** Lanes and trade-id prefixes of tiles the registry marks platform_relay_eligible. */
export const RELAY_ELIGIBLE_TILE_LANES: readonly string[] = [];

export const RELAY_ELIGIBLE_TILE_ID_PREFIXES: readonly string[] = [];

/** Tiles whose exit policy reduces a position in parts; relay refuses them until exchange-side reductions are proven. */
export const PARTIAL_EXIT_TILE_LANES: readonly string[] = [];

export const PARTIAL_EXIT_TILE_ID_PREFIXES: readonly string[] = [];

export const RETIRED_TILE_LANES: readonly string[] = ["CONTINUOUS", "FAMILY_ADAPTIVE_REGIME", "FAMILY_ADAPTIVE_REGIME_LADDER", "FAMILY_ADAPTIVE_REGIME_LADDER_BE", "FAMILY_ATR_TARGET_2_5", "FAMILY_ATR_TRAIL", "FAMILY_CHANDELIER_3", "FAMILY_HYBRID_RUNNER", "FAMILY_MFE_GIVEBACK", "FAMILY_TREND_FADE_60", "FAMILY_TREND_FADE_60_COMMITTED", "FAMILY_TREND_FADE_60_LADDER", "FAMILY_XVENUE_LEAD_60S", "FAMILY_XVENUE_PREMIUM_60S", "OFFSET_029_ATR_PROTECTED", "OFFSET_029_ATR_REGIME", "OFFSET_029_ATR_TP_25", "PROTECTED_W234_SCENARIO_C"];

export const COMPARISON_BENCHMARK_LANE = null;

"""V2: gate CONTINUOUS on shared chain timestamp so all 3 lanes cascade at 60s intervals."""
import re
path = r'C:\Users\user\Desktop\Final Bots\doxedcryptofounder\services\btc-conservative-agent\bot.py'
with open(path, encoding='utf-8', errors='replace') as f:
    content = f.read()

# 1. Update comment block via regex
if re.search(r'TYPE_B_HUNTER_V1 at T\+60s offset', content):
    content = re.sub(
        r'# .+ v11\.6 Independent Research Tile AI Dispatch .+\n'
        r'# TYPE_B_HUNTER_V1 at T\+60s offset, SR_MICRO_TILE_V1 at T\+120s offset\.\n'
        r'# Each tile calls evaluate_signal_with_ai with its own custom prompt\.\n'
        r'# Shadow-only \(no live orders\) while tile toggle is OFF\.\n'
        r'#\n'
        r'# Cascading cadence: a shared chain timestamp enforces .+ between ANY\n'
        r'# two research-tile ticks\.  TYPE_B fires first \(called first in engine_loop\),\n'
        r'# SR_MICRO naturally falls 60s later .+ producing the T\+60 / T\+120 pattern\.',
        '# -- v11.6 Independent Research Tile AI Dispatch --\n'
        '# CONTINUOUS -> TYPE_B_HUNTER_V1 -> SR_MICRO_TILE_V1 -> CONTINUOUS ...\n'
        '# Shared chain timestamp enforces >=60s between ANY two AI ticks.\n'
        '# Each tile calls evaluate_signal_with_ai with its own custom prompt.\n'
        '# Shadow-only (no live orders) while tile toggle is OFF.\n'
        '#\n'
        '# Order in engine_loop: periodic pipeline (CONTINUOUS), then TYPE_B, then SR_MICRO.\n'
        '# All three gate on _research_tile_chain_ts + 60 -- producing a clean 3-step cascade.',
        content, count=1
    )
    print("Comment updated")
else:
    print("Comment marker NOT FOUND")

# 2. Gate periodic pipeline on chain_ts
# Build the old/new strings explicitly
old_lines = [
    '            if time.time() - last_pipeline_run >= MIN_PIPELINE_INTERVAL:',
    '                logger.info("[PERIODIC PIPELINE] forcing detect_event_light for analyzer data [PIPELINE ENFORCEMENT]")',
    '                event = detect_event_light()',
    '                if event and event.get("event_trigger"):',
    '                    process_signal(event)',
    '                elif (',
    '                    _sole_ai_research_mode()',
    '                    and any_combo_execution_enabled(research_lane_enabled_map(), continuous_ai_research_enabled())',
    '                    and ai_cooldown_remaining_sec(RESEARCH_LANE_AI_SCAN) == 0',
    '                ):',
    '                    features = build_full_feature_snapshot()',
    '                    if features:',
    '                        edge_score = compute_edge_score(features)',
    '                        if round(edge_score, 1) >= 0.0:',
    '                            process_signal({',
    '                                "event_trigger": True,',
    '                                "research_lane": RESEARCH_LANE_AI_SCAN,',
    '                                "edge_trigger_reason": "PERIODIC_RESEARCH_AI",',
    '                                "edge_score": round(edge_score, 1),',
    '                                "price": nz(state.get("price")),',
    '                                "timestamp": utc_iso(),',
    '                                "features": features,',
    '                            })',
]
old_periodic = '\n'.join(old_lines)

new_lines = [
    '            if time.time() - last_pipeline_run >= MIN_PIPELINE_INTERVAL:',
    '                now = time.time()',
    '                if now >= _research_tile_chain_ts + 60:',
    '                    logger.info("[PERIODIC PIPELINE] forcing detect_event_light for analyzer data [PIPELINE ENFORCEMENT]")',
    '                    event = detect_event_light()',
    '                    if event and event.get("event_trigger"):',
    '                        process_signal(event)',
    '                        _research_tile_chain_ts = time.time()',
    '                    elif (',
    '                        _sole_ai_research_mode()',
    '                        and any_combo_execution_enabled(research_lane_enabled_map(), continuous_ai_research_enabled())',
    '                        and ai_cooldown_remaining_sec(RESEARCH_LANE_AI_SCAN) == 0',
    '                    ):',
    '                        features = build_full_feature_snapshot()',
    '                        if features:',
    '                            edge_score = compute_edge_score(features)',
    '                            if round(edge_score, 1) >= 0.0:',
    '                                process_signal({',
    '                                    "event_trigger": True,',
    '                                    "research_lane": RESEARCH_LANE_AI_SCAN,',
    '                                    "edge_trigger_reason": "PERIODIC_RESEARCH_AI",',
    '                                    "edge_score": round(edge_score, 1),',
    '                                    "price": nz(state.get("price")),',
    '                                    "timestamp": utc_iso(),',
    '                                    "features": features,',
    '                                })',
    '                                _research_tile_chain_ts = time.time()',
]
new_periodic = '\n'.join(new_lines)

if old_periodic in content:
    content = content.replace(old_periodic, new_periodic)
    print("Periodic pipeline gated on chain_ts")
else:
    # Search for the marker to understand what's nearby
    idx = content.find('"[PERIODIC PIPELINE] forcing detect_event_light"')
    if idx > 0:
        start = max(0, idx - 100)
        end = min(len(content), idx + 700)
        print("Context around marker:")
        chunk = content[start:end]
        for i, line in enumerate(chunk.split('\n'), 1):
            print(f"  {i}: {repr(line[:120])}")
    else:
        print("Marker not found at all")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done writing.")

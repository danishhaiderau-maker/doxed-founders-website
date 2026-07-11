"""Fix research tile timing — enforce ≥60s gap between tiles via shared chain timestamp."""
path = r'C:\Users\user\Desktop\Final Bots\doxedcryptofounder\services\btc-conservative-agent\bot.py'
with open(path, encoding='utf-8', errors='replace') as f:
    content = f.read()

# 1. Replace cooldown dict with chain timestamp
content = content.replace(
    '# Shared cooldown state for independent research AI ticks.',
    '# Shared chain timestamp — enforces \u226560s gap between ANY two research ticks.')
content = content.replace(
    '_research_tile_cooldown_until = {}  # {lane: timestamp_until}',
    '_research_tile_chain_ts = time.time() - 120  # bootstrap — allow first tick immediately')

# 2. Fix TYPE_B_HUNTER_V1 — chain check + SR_MICRO guard + global
old_tb_block = '''    lane = RESEARCH_LANE_TYPE_B_HUNTER_V1
    if not is_research_data_collection():
        return
    if not is_combo_execution_lane(lane):
        return
    # Cooldown: 120s between ticks per tile
    now = time.time()
    if now < _research_tile_cooldown_until.get(lane, 0):
        return
    if _lane_pipeline_running(lane):
        return
    if _lane_pipeline_running(RESEARCH_LANE_AI_SCAN):
        return
    _research_tile_cooldown_until[lane] = now + 120'''

new_tb_block = '''    lane = RESEARCH_LANE_TYPE_B_HUNTER_V1
    global _research_tile_chain_ts
    if not is_research_data_collection():
        return
    if not is_combo_execution_lane(lane):
        return
    now = time.time()
    if now < _research_tile_chain_ts + 60:
        return
    if _lane_pipeline_running(lane):
        return
    if _lane_pipeline_running(RESEARCH_LANE_AI_SCAN):
        return
    if _lane_pipeline_running(RESEARCH_LANE_SR_MICRO_TILE_V1):
        return
    _research_tile_chain_ts = now'''

content = content.replace(old_tb_block, new_tb_block)

# 3. Fix SR_MICRO_TILE_V1 — chain check + TYPE_B guard + global
old_sr_block = '''    lane = RESEARCH_LANE_SR_MICRO_TILE_V1
    if not is_research_data_collection():
        return
    if not is_combo_execution_lane(lane):
        return
    # Cooldown: 120s between ticks per tile
    now = time.time()
    if now < _research_tile_cooldown_until.get(lane, 0):
        return
    if _lane_pipeline_running(lane):
        return
    if _lane_pipeline_running(RESEARCH_LANE_AI_SCAN):
        return
    _research_tile_cooldown_until[lane] = now + 120'''

new_sr_block = '''    lane = RESEARCH_LANE_SR_MICRO_TILE_V1
    global _research_tile_chain_ts
    if not is_research_data_collection():
        return
    if not is_combo_execution_lane(lane):
        return
    now = time.time()
    if now < _research_tile_chain_ts + 60:
        return
    if _lane_pipeline_running(lane):
        return
    if _lane_pipeline_running(RESEARCH_LANE_AI_SCAN):
        return
    if _lane_pipeline_running(RESEARCH_LANE_TYPE_B_HUNTER_V1):
        return
    _research_tile_chain_ts = now'''

content = content.replace(old_sr_block, new_sr_block)

# 4. Update log message "offset=60s" → "gap=60s+"
content = content.replace(
    'independent tick offset=60s edge=',
    'independent tick gap=60s+ edge=')
content = content.replace(
    'independent tick offset=120s edge=',
    'independent tick gap=60s+ edge=')

# Write
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Applied all timing fixes:")
print("  - Replaced _research_tile_cooldown_until with _research_tile_chain_ts")
print("  - TYPE_B: 60s chain gate + SR_MICRO guard + global")
print("  - SR_MICRO: 60s chain gate + TYPE_B guard + global")
print("  - Log messages updated")

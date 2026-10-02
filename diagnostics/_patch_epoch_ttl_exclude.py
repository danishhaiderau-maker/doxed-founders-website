from pathlib import Path


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    changed = False
    old_fcm = """    if fcm and not fresh_collection_reset:
        for key in (
            \"collector_v22_epoch_ts\",
            \"collector_v22_epoch_id\",
            \"legacy_collector_version\",
        ):
            if prev.get(key) not in (None, \"\"):
                payload[key] = prev.get(key)
    if fresh_collection_reset and fresh_start is not None:"""
    new_fcm = """    if not fresh_collection_reset:
        for key in (
            \"collector_v22_epoch_ts\",
            \"collector_v22_epoch_id\",
            \"legacy_collector_version\",
        ):
            if prev.get(key) not in (None, \"\"):
                payload[key] = prev.get(key)
        if not fcm:
            for key in (
                \"fresh_collection_start_time\",
                \"fresh_collection_start_iso\",
                \"fresh_collection_start_iso_utc\",
            ):
                if prev.get(key) not in (None, \"\"):
                    payload[key] = prev.get(key)
    if fresh_collection_reset and fresh_start is not None:"""
    if old_fcm in text:
        text = text.replace(old_fcm, new_fcm, 1)
        changed = True
        print(path.name, "epoch-preserve patched")
    else:
        print(path.name, "epoch-preserve already applied or missing")

    if "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS = 150.0" in text:
        text = text.replace(
            "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS = 150.0",
            "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS = 2 * 60 * 60",
            1,
        )
        changed = True
        print(path.name, "TTL patched to 2h")
    elif "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS = 2 * 60 * 60" in text:
        print(path.name, "TTL already 2h")
    else:
        print(path.name, "TTL pattern missing")

    block_start = text.find("_DATA_SYNC_EXCLUDED_DIR_NAMES = frozenset({")
    if block_start >= 0:
        block_end = text.find("})", block_start)
        block = text[block_start:block_end]
        if "recovery_receipts" not in block:
            text = text[:block_end] + '    "recovery_receipts",\n' + text[block_end:]
            changed = True
            print(path.name, "recovery_receipts exclude appended")
        else:
            print(path.name, "recovery_receipts already excluded")
    else:
        print(path.name, "exclude dir block missing")

    if changed:
        path.write_text(text, encoding="utf-8")


root = Path(r"C:\DoxxedCrypto\btc-v31-epoch-preserve-wt")
for rel in [
    "services/btc-conservative-agent/bot.py",
    "services/btc-signal-engine/engine.py",
]:
    p = root / rel
    if p.exists():
        patch(p)
    else:
        print("missing", rel)

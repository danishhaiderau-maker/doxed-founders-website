"""Storage UI must not confuse cached inventory with current filesystem use."""
from pathlib import Path
import json
import subprocess

from test_data_size_endpoint import DataSizeEndpointTests, bot


class StorageFreshnessTests(DataSizeEndpointTests):
    def test_progress_is_cached_and_observation_is_separate(self):
        self._state("BUILDING")
        bot._data_sync_async_inventory.update(worker_phase="WRITING_PAGES",
                                             worker_pages_written=12, worker_pages_total=42)
        body = self._get(used_mb=500).get_json()
        self.assertEqual(body["filesystem_used_mb"], 500)
        self.assertEqual(body["inventory_transferable_mb"], 430)
        self.assertEqual(body["runtime_size_status"], "STALE_REVALIDATING")
        self.assertEqual(body["inventory_progress"], {
            "phase": "WRITING_PAGES", "pages_written": 12, "pages_total": 42})
        self.assertEqual(body["inventory_generated_at"], "2026-09-03T00:00:00Z")
        self.assertGreater(body["computed_at"], 0)


def test_storage_ui_labels_and_server_observation_contract():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    start = source.index("const inventoryStatus = String(body.runtime_size_status")
    end = source.index("// Top-5 file table.", start)
    ui = source[start:end]
    assert "mbEl.textContent = body.filesystem_used_mb" in ui
    assert "Cached transferable inventory:" in ui
    assert "body.inventory_generated_at" in ui
    assert "body.inventory_progress" in ui
    assert "lastEl.textContent = observedText" in ui
    assert "new Date().toLocaleTimeString()" not in ui


def test_actual_storage_javascript_renders_current_stale_and_missing_values():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    start = source.index("const mbEl = document.getElementById('dataSizeFlyMb');")
    end = source.index("// Top-5 file table.", start)
    block = source[start:end]
    harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
const document = {getElementById(id) {
  return nodes[id] ||= {textContent: 'OLD', style: {}};
}};
const barEl = document.getElementById('dataSizeVolumeBar');
const badgeEl = document.getElementById('dataSizeCleanupBadge');
let cleanupCalls = [];
function _setDataSizeCleanup(pct) { cleanupCalls.push(pct); }
const render = new Function('body', 'document', 'barEl', 'badgeEl', '_setDataSizeCleanup', BLOCK);
function run(body) {render(body, document, barEl, badgeEl, _setDataSizeCleanup);}
run({filesystem_used_mb:500, filesystem_free_mb:524, volume_total_mb:1024,
  volume_pct:48.8, inventory_transferable_mb:430, runtime_size_mb:430,
  runtime_size_status:'STALE_REVALIDATING', computed_at:1788393660,
  inventory_generated_at:'2026-09-03T00:00:00Z', inventory_refreshing:true,
  inventory_progress:{phase:'WRITING_PAGES',pages_written:12,pages_total:42}});
assert.equal(nodes.dataSizeFlyMb.textContent,'500.0');
assert.match(nodes.dataSizeInventoryStatus.textContent,/430.0 MiB.*STALE_REVALIDATING/);
assert.match(nodes.dataSizeInventoryStatus.textContent,/generated: 2026-09-03T00:00:00.000Z/);
assert.match(nodes.dataSizeInventoryStatus.textContent,/age: 60s/);
assert.match(nodes.dataSizeInventoryStatus.textContent,/refresh: running.*pages: 12\/42/);
assert.equal(nodes.dataSizeLastCheck.textContent,'2026-09-03T00:01:00.000Z');
assert.match(nodes.dataSizeFilesystemStatus.textContent,/Free: 524.0 MiB/);
assert.equal(barEl.style.width,'48.8%');
run({});
assert.equal(nodes.dataSizeFlyMb.textContent,'-');
assert.equal(nodes.dataSizeVolumeTotal.textContent,'-');
assert.equal(nodes.dataSizeVolumePct.textContent,'-');
assert.equal(nodes.dataSizeLastCheck.textContent,'unavailable');
assert.match(nodes.dataSizeInventoryStatus.textContent,/inventory: unavailable.*UNAVAILABLE.*age: unavailable/);
assert.match(nodes.dataSizeInventoryStatus.textContent,/pages: \?\/\?/);
assert.match(nodes.dataSizeFilesystemStatus.textContent,/Free: unavailable/);
assert.equal(barEl.style.width,'0%');
assert.deepEqual(cleanupCalls,[48.8,null]);
console.log('storage JS runtime assertions passed');
'''.replace('BLOCK', json.dumps(block))
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'storage JS runtime assertions passed' in result.stdout

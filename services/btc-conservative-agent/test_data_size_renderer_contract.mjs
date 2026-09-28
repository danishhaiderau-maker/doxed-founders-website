import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./bot.py', import.meta.url), 'utf8');
const match = source.match(/    function _dataSizeInventoryView\(body\) \{[\s\S]*?\n    \}/);
assert.ok(match, 'storage inventory renderer helper must remain extractable');
const context = {};
vm.runInNewContext(`${match[0]}\nthis.renderInventory = _dataSizeInventoryView;`, context);

test('current inventory renders its transferable size', () => {
  const view = context.renderInventory({ runtime_size_status: 'CURRENT', runtime_size_mb: 2452.2 });
  assert.equal(view.sizeText, '2452.2');
  assert.match(view.statusText, /current transferable/);
});

test('stale cached inventory never renders as current runtime size', () => {
  const view = context.renderInventory({ runtime_size_status: 'STALE_REVALIDATING', runtime_size_mb: 2452.2 });
  assert.equal(view.sizeText, '-');
  assert.match(view.statusText, /STALE cached transfer inventory/);
  assert.match(view.statusText, /not current runtime size/);
});

test('unknown inventory renders a dash and explicit unknown state', () => {
  const view = context.renderInventory({ runtime_size_status: 'UNAVAILABLE', runtime_size_mb: 2452.2 });
  assert.equal(view.sizeText, '-');
  assert.match(view.statusText, /current runtime size unknown/);
});

test('CURRENT with a missing, blank, nonfinite, or negative size fails closed', () => {
  for (const runtime_size_mb of [null, undefined, '', '   ', Number.NaN, Number.POSITIVE_INFINITY, -1]) {
    const view = context.renderInventory({ runtime_size_status: 'CURRENT', runtime_size_mb });
    assert.equal(view.current, false);
    assert.equal(view.status, 'UNAVAILABLE');
    assert.equal(view.sizeText, '-');
    assert.match(view.statusText, /current runtime size unknown/);
    assert.notEqual(view.statusColor, '#3fb950');
  }
});

test('dashboard distinguishes filesystem use and explains local copy scope', () => {
  assert.match(source, /id="dataSizeFilesystemUsed"/);
  assert.match(source, /moves the prior active mirror into local quarantine; it does not delete that local copy/);
  assert.match(source, /existing local sync mirror is left untouched/);
  assert.match(source, /retaining protected credentials, accounting, and recovery state/);
  assert.match(source, /Current transferable research inventory/);
});

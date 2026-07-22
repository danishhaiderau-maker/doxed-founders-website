import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(__dirname, '..');

describe('Founder desktop companion', () => {
  it('ships four original transparent state assets', () => {
    for (const asset of [
      'dragon-idle.png',
      'dragon-working.png',
      'dragon-success-v3.png',
      'dragon-attention.png',
    ]) {
      const file = path.join(root, 'companion-assets', asset);
      assert.ok(fs.existsSync(file), `${asset} must ship with Founder Node`);
      const png = fs.readFileSync(file);
      assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
      assert.ok(png.length > 100_000, `${asset} should contain the production dragon artwork`);
    }
  });

  it('uses a transparent draggable surface with real work states', () => {
    const html = fs.readFileSync(path.join(root, 'companion-assets', 'companion.html'), 'utf8');
    const windowSource = fs.readFileSync(path.join(root, 'src', 'desktop-companion.ts'), 'utf8');
    assert.match(html, /background:\s*transparent/);
    assert.match(html, /-webkit-app-region:\s*no-drag/);
    assert.match(html, /dragon-working\.png/);
    assert.match(html, /dragon-success-v3\.png/);
    assert.match(html, /@keyframes fly/);
    assert.match(html, /task-bubble/);
    assert.doesNotMatch(html, /\.mp4/);
    assert.match(windowSource, /screen\.getCursorScreenPoint\(\)/);
    assert.match(windowSource, /DRAGON_HIT_AREA/);
    assert.match(windowSource, /setIgnoreMouseEvents\(!interactive/);
    assert.match(windowSource, /setShape\(/);
    assert.match(windowSource, /founder-companion-drag-move/);
    assert.match(windowSource, /snapToNearbyEdge/);
    assert.match(windowSource, /lastDisplayId/);
    assert.match(windowSource, /positions\[String\(display\.id\)\]/);
    assert.match(windowSource, /Hide until next task/);
    assert.match(windowSource, /hiddenUntilNextTask/);
    assert.match(windowSource, /pendingUpdateSnapshot/);
    assert.match(windowSource, /lastTaskSnapshot\.state !== 'idle'/);
    assert.match(windowSource, /updateDesktopCompanionUpdate/);
    assert.match(windowSource, /Reduce motion/);
    assert.match(html, /setPointerCapture/);
    assert.match(html, /planning/);
    assert.match(html, /coordinating/);
    assert.match(html, /verifying/);
    assert.match(html, /showMenu/);
  });
});

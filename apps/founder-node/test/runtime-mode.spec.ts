import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';

import { isEmbeddedRelayMode } from '../src/runtime-mode.js';

const root = path.resolve(__dirname, '..');

describe('Founder Node runtime mode', () => {
  it('uses embedded mode when Founder IDE launches the relay', () => {
    assert.equal(isEmbeddedRelayMode({ FOUNDER_NODE_EMBEDDED: '1' }, []), true);
    assert.equal(isEmbeddedRelayMode({}, ['--embedded-founder-ide']), true);
  });

  it('keeps standalone behavior for legacy direct launches', () => {
    assert.equal(isEmbeddedRelayMode({}, []), false);
  });

  it('suppresses the standalone firewall dialog inside Founder IDE', () => {
    const mainSource = fs.readFileSync(path.join(root, 'src', 'main.ts'), 'utf8');
    const firewallSource = fs.readFileSync(path.join(root, 'src', 'firewall-helper.ts'), 'utf8');
    assert.match(mainSource, /embeddedMode:\s*EMBEDDED_RELAY_MODE/g);
    assert.match(firewallSource, /if\s*\(options\.embeddedMode\)\s*return/);
  });
});

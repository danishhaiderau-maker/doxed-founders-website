import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { isEmbeddedRelayMode } from '../src/runtime-mode.js';

describe('Founder Node runtime mode', () => {
  it('uses embedded mode when Founder IDE launches the relay', () => {
    assert.equal(isEmbeddedRelayMode({ FOUNDER_NODE_EMBEDDED: '1' }, []), true);
    assert.equal(isEmbeddedRelayMode({}, ['--embedded-founder-ide']), true);
  });

  it('keeps standalone behavior for legacy direct launches', () => {
    assert.equal(isEmbeddedRelayMode({}, []), false);
  });
});

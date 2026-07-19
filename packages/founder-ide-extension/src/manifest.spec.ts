import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { FOUNDER_TOOL_IDS } from './tool-names';

interface ExtensionManifest {
  contributes?: {
    languageModelTools?: Array<{ name?: string }>;
  };
}

describe('Founder IDE extension manifest', () => {
  it('contributes runtime-compatible tool ids that match the registered tools', () => {
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as ExtensionManifest;
    const contributedNames = (manifest.contributes?.languageModelTools ?? []).map(
      (tool) => tool.name,
    );
    const registeredNames = Object.values(FOUNDER_TOOL_IDS);

    assert.deepEqual(contributedNames, registeredNames);
    assert.equal(new Set(contributedNames).size, contributedNames.length);
    for (const name of contributedNames) {
      assert.match(name ?? '', /^[\w-]+$/);
    }
  });
});

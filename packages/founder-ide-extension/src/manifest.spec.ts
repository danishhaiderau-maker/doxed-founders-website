import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { FOUNDER_TOOL_IDS } from './tool-names';

interface ExtensionManifest {
  contributes?: {
    languageModelTools?: Array<{ name?: string }>;
    commands?: Array<{ command?: string }>;
    viewsContainers?: {
      activitybar?: Array<{ id?: string; title?: string; icon?: string }>;
    };
    views?: Record<string, Array<{ id?: string; name?: string; type?: string }>>;
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

  it('contributes a Founder-owned Activity Bar control surface', () => {
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as ExtensionManifest;
    const activityContainer = manifest.contributes?.viewsContainers?.activitybar?.find(
      (container) => container.id === 'founderOs',
    );
    const hub = manifest.contributes?.views?.founderOs?.find(
      (view) => view.id === 'founderOs.hub',
    );
    const commands = new Set(
      (manifest.contributes?.commands ?? []).map((command) => command.command),
    );

    assert.equal(activityContainer?.title, 'Founder');
    assert.equal(activityContainer?.icon, 'resources/founder.svg');
    assert.equal(hub?.name, 'Founder');
    assert.equal(hub?.type, 'webview');
    assert.ok(commands.has('founderOs.signIn'));
    assert.ok(commands.has('founderOs.signOut'));
    assert.ok(commands.has('founderOs.openConnections'));
    assert.ok(commands.has('founderOs.openSettings'));
  });
});

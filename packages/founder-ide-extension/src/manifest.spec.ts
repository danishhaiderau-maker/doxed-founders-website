import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { FOUNDER_TOOL_IDS } from './tool-names';

interface ExtensionManifest {
  activationEvents?: string[];
  contributes?: {
    authentication?: Array<{ id?: string; label?: string }>;
    languageModelTools?: Array<{ name?: string }>;
    commands?: Array<{ command?: string }>;
    viewsContainers?: {
      activitybar?: Array<{ id?: string; title?: string; icon?: string }>;
    };
    views?: Record<string, Array<{ id?: string; name?: string; type?: string; visibility?: string }>>;
    menus?: Record<string, Array<{ command?: string; when?: string }>>;
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
    for (const asset of [
      'dragon-idle.png',
      'dragon-working.png',
      'dragon-success-v3.png',
      'dragon-attention.png',
    ]) {
      assert.ok(
        existsSync(join(__dirname, '..', 'resources', 'dragon', asset)),
        `${asset} must ship with the Founder Dragon`,
      );
    }
    assert.deepEqual(
      manifest.activationEvents,
      ['onView:founderOs.hub', 'onStartupFinished'],
      'Founder Home activates on demand and Founder Node starts after workbench startup',
    );
    assert.deepEqual(manifest.contributes?.authentication, [
      { id: 'founderOs', label: 'Founder' },
    ]);
    assert.ok(commands.has('founderOs.signIn'));
    assert.ok(commands.has('founderOs.signOut'));
    assert.ok(commands.has('founderOs.openConnections'));
    assert.ok(commands.has('founderOs.openSettings'));
    assert.ok(commands.has('founderOs.openProjects'));
    assert.ok(commands.has('founderOs.transcribeVoice'));
    assert.ok(commands.has('founderOs.refreshProjectContext'));
    const preferences = manifest.contributes?.menus?.['menubar/preferences'] ?? [];
    assert.ok(
      preferences.some((item: { command?: string }) => item.command === 'founderOs.openSettings'),
      'Founder Settings must be available from the global settings menu',
    );
  });

  it('keeps the Founder shortcut rail complete, ordered, and wired', () => {
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as ExtensionManifest;
    const expectedContainers = [
      ['founderOs', [
        'founderOs.hub',
        'founderOs.agents',
        'founderOs.ship',
        'founderOs.node',
        'founderOs.connections',
        'founderOs.remote',
      ]],
    ] as const;
    const containers = manifest.contributes?.viewsContainers?.activitybar ?? [];
    const commands = new Set(
      (manifest.contributes?.commands ?? []).map((command) => command.command),
    );

    assert.deepEqual(
      containers.map((container) => container.id),
      expectedContainers.map(([containerId]) => containerId),
    );
    for (const [containerId, viewIds] of expectedContainers) {
      const container = containers.find((item) => item.id === containerId);
      assert.ok(container?.icon);
      assert.ok(existsSync(join(__dirname, '..', container.icon)));
      assert.deepEqual(
        manifest.contributes?.views?.[containerId]?.map((view) => view.id),
        [...viewIds],
      );
    }
    assert.equal(containers.length, 1, 'Founder navigation should use one labelled home');
    for (const view of manifest.contributes?.views?.founderOs?.slice(1) ?? []) {
      assert.equal(view.visibility, 'collapsed', `${view.id} should use progressive disclosure`);
    }

    for (const item of manifest.contributes?.menus?.['view/title'] ?? []) {
      assert.ok(commands.has(item.command), `${item.command} must be contributed`);
    }
    for (const command of [
      'founderOs.openCompanion',
      'founderOs.openAgents',
      'founderOs.openShip',
      'founderOs.openNodeView',
      'founderOs.openConnectionsView',
      'founderOs.openRemoteView',
      'founderOs.openRemoteControl',
      'founderOs.refreshShortcuts',
    ]) {
      assert.ok(commands.has(command), `${command} must be contributed`);
    }
  });

  it('keeps Remote and Connect visible while legacy editor chrome is progressive', () => {
    const hubSource = readFileSync(join(__dirname, 'founder-hub.ts'), 'utf8');
    const extensionSource = readFileSync(join(__dirname, 'extension.ts'), 'utf8');

    for (const label of [
      '<strong>New chat</strong>',
      '<strong>Projects</strong>',
      '<strong>Chats</strong>',
      '<strong>Agents</strong>',
      '<strong>Graph</strong>',
      '<strong>Remote</strong>',
      '<strong>Connect</strong>',
    ]) {
      assert.ok(hubSource.includes(label), `${label} must be visible in Founder navigation`);
    }
    assert.match(extensionSource, /applyFounderInterfaceMode\(true\)/);
    assert.match(
      extensionSource,
      /executeCommand\('workbench\.action\.closePanel'\)/,
    );
    assert.match(extensionSource, /definition\.activityBarLocation/);
    assert.match(extensionSource, /definition\.menuBarVisibility/);
    assert.match(hubSource, /Developer mode/);
    assert.match(hubSource, /Founder mode/);
    assert.match(hubSource, /body\s*\{[\s\S]*?padding:\s*0;/);
    assert.match(hubSource, /overflow-x:\s*hidden;/);
    assert.match(hubSource, /@media\s*\(max-width:\s*180px\)/);
    assert.match(hubSource, /\.nav-copy span,[\s\S]*?\.tool-item span,[\s\S]*?\.summary-value[\s\S]*?display:\s*none;/);
    assert.match(
      hubSource,
      /case 'openConnections':[\s\S]*?founderOs\.openSettings', 'connections'/,
    );
    assert.match(
      hubSource,
      /case 'openRemote':[\s\S]*?founderOs\.openRemoteControl/,
    );
    assert.match(
      hubSource,
      /case 'openProjects':[\s\S]*?founderOs\.openProjects/,
    );
    assert.match(
      hubSource,
      /case 'openChats':[\s\S]*?void\.historyAction/,
    );
  });

  it('ships supported Founder-first layout settings as extension defaults', () => {
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as {
      contributes?: {
        configurationDefaults?: Record<string, unknown>;
      };
    };
    assert.deepEqual(manifest.contributes?.configurationDefaults, {
      'workbench.activityBar.location': 'hidden',
      'founderOs.interfaceMode': 'founder',
      'founderOs.advancedIdeTools': false,
    });
  });
});

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const read = (relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');
const manifest = JSON.parse(read('MANIFEST.json'));

describe('Founder IDE upstream overlay', () => {
  it('ships the native composer override through the manifest', () => {
    assert.ok(
      manifest.files.some(
        (entry) =>
          entry.dest ===
          'src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/SidebarChat.tsx',
      ),
    );
  });

  it('removes visible upstream branding from personal AI settings', () => {
    const manifestPaths = new Set(manifest.files.map((entry) => entry.dest));
    assert.ok(manifestPaths.has('src/vs/workbench/contrib/void/browser/sidebarActions.ts'));
    assert.ok(manifestPaths.has('src/vs/workbench/contrib/void/browser/voidSettingsPane.ts'));
    assert.ok(manifestPaths.has('src/vs/workbench/contrib/void/browser/react/src/void-settings-tsx/Settings.tsx'));

    const settings = read('src/vs/workbench/contrib/void/browser/react/src/void-settings-tsx/Settings.tsx');
    const pane = read('src/vs/workbench/contrib/void/browser/voidSettingsPane.ts');
    const actions = read('src/vs/workbench/contrib/void/browser/sidebarActions.ts');
    assert.match(settings, />Personal AI</);
    assert.doesNotMatch(settings, /Void's Settings/);
    assert.doesNotMatch(pane, /Void\\?'s Settings/);
    assert.doesNotMatch(actions, /Void's Settings/);
  });

  it('uses Founder Gateway only for Founder-managed aliases', () => {
    const source = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendLLMMessage.ts',
    );
    assert.match(source, /modelName\.startsWith\('founder-os-'\)/);
    assert.match(source, /founderOsEnabled\(\) && isFounderManagedSelection/);
    assert.match(source, /resolveFounderProviderProfile/);
    assert.match(source, /effectiveModelName/);
    assert.match(source, /Personal AI/);
    assert.match(source, /outside managed quota/);
  });

  it('advertises the current DeepSeek V4 context contract for every managed route', () => {
    const capabilities = read('src/vs/workbench/contrib/void/common/modelCapabilities.ts');
    for (const alias of [
      'founder-os-auto',
      'founder-os-fast',
      'founder-os-reasoning',
      'founder-os-code',
    ]) {
      const start = capabilities.indexOf(`'${alias}':`);
      assert.ok(start >= 0, `${alias} must be present`);
      assert.match(capabilities.slice(start, start + 260), /contextWindow:\s*1_000_000/);
    }
  });

  it('ships encrypted remembered personal provider profiles', () => {
    const settings = read(
      'src/vs/workbench/contrib/void/browser/react/src/void-settings-tsx/Settings.tsx',
    );
    const profiles = read(
      'src/vs/workbench/contrib/void/common/founderProviderProfiles.ts',
    );
    assert.match(settings, /Profile name \(for chat dropdown\)/);
    assert.match(settings, /Base URL/);
    assert.match(settings, /API key/);
    assert.doesNotMatch(profiles, /FOUNDER_PROVIDER_PROFILE_LIMIT/);
    assert.doesNotMatch(settings, /Maximum five personal AI profiles/);
    assert.match(profiles, /founder-os-/);
    const bridge = read(
      'src/vs/workbench/contrib/void/browser/founderPersonalAiActions.ts',
    );
    for (const command of [
      'founder.personalAi.save',
      'founder.personalAi.select',
      'founder.personalAi.enable',
      'founder.personalAi.delete',
      'founder.managedAi.select',
    ]) {
      assert.ok(bridge.includes(command));
    }
    assert.match(bridge, /Remote Personal AI providers must use HTTPS/);
    assert.match(bridge, /BLOCKED_HEADERS/);
  });

  it('marks added files so the overlay can be applied repeatedly', () => {
    const added = manifest.files.filter((entry) => entry.mode === 'add');
    assert.ok(added.length > 0);
    for (const entry of added) {
      assert.ok(entry.marker, `${entry.dest} needs an ownership marker`);
      assert.match(read(entry.src), new RegExp(entry.marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
  });

  it('adds route receipts and Founder identity to managed responses', () => {
    const source = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    assert.match(source, /You are Founder AI/);
    assert.match(source, /\*\*Founder route\*\*/);
    assert.match(source, /founder_os_metadata: true/);
    assert.match(source, /Free: Flash/);
  });

  it('keeps voice input and personal AI next to the native composer', () => {
    const source = read(
      'src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/SidebarChat.tsx',
    );
    assert.match(source, /Add or manage personal AI/);
    assert.match(source, /Start voice input/);
    assert.match(source, /founderSpeechRecognitionConstructor/);
    assert.match(source, /founderOs\.openSettings/);
    assert.match(source, /founderOs\.companionState/);
    assert.match(source, /Voice text is ready/);
  });

  it('coordinates the native chat with other active Founder tasks', () => {
    const sender = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendLLMMessage.ts',
    );
    const gateway = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    const coordination = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeCoordination.ts',
    );
    assert.match(sender, /loggingExtras\?\.workspacePath/);
    assert.match(gateway, /beginNativeCoordination/);
    assert.match(gateway, /coordination\.refresh\(\)/);
    assert.match(coordination, /Live agent coordination/);
    assert.match(coordination, /\.founder-ide', 'coordination/);
    assert.match(coordination, /COORDINATE: goals substantially overlap/);
  });

  it('keeps Team mode bounded to read-only advisers and one editing owner', () => {
    const gateway = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    const team = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeTeam.ts',
    );
    assert.match(gateway, /Promise\.all/);
    assert.match(gateway, /model: 'founder-os-fast'/);
    assert.match(gateway, /formatNativeTeamAdvice/);
    assert.match(team, /Two read-only advisers|read-only Context Scout/i);
    assert.match(team, /sole editing owner/i);
    assert.match(team, /mode !== 'team' \|\| chatMode !== 'agent'/);
  });

  it('escalates Founder Auto only from deterministic evidence', () => {
    const gateway = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    const routing = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeRouting.ts',
    );
    assert.match(gateway, /requestedModel === 'founder-os-auto'/);
    assert.match(gateway, /Auto requested Pro/);
    assert.match(routing, /failed_verification/);
    assert.match(routing, /signals >= 2/);
    assert.match(routing, /\[exit code/);
  });

  it('preserves native tools, reasoning, and the terminal completion marker', () => {
    const source = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    assert.match(source, /availableTools\(chatMode, mcpTools\)/);
    assert.match(source, /body\.tools = tools/);
    assert.match(source, /choiceDelta\?\.tool_calls/);
    assert.match(source, /reasoning_content/);
    assert.match(source, /completedToolCall/);
    assert.match(source, /Stream ended before the completion marker/);
    assert.match(source, /tool_calls = raw\.tool_calls/);
  });

  it('patches native chat metadata with the active workspace root', () => {
    const applyScript = read('../../../../scripts/apply-founder-customizations.ps1');
    assert.match(applyScript, /native chat workspace coordination/);
    assert.match(applyScript, /workspacePath: this\._workspaceContextService/);
  });

  it('owns every visible upstream brand boundary and disables the legacy updater', () => {
    const applyScript = read('../../../../scripts/apply-founder-customizations.ps1');
    for (const founderLabel of [
      'Founder Settings',
      'Open Founder Chat',
      'Founder Agent',
      'Founder IDE: Generate Commit Message',
      'Founder IDE: Quick Edit',
      'Founder IDE: Check for Updates',
    ]) {
      assert.ok(applyScript.includes(founderLabel), `missing ${founderLabel}`);
    }
    assert.match(applyScript, /false && registerAction2/);
    assert.match(applyScript, /false && registerWorkbenchContribution2/);
    assert.match(applyScript, /Founder Node owns the signed manifest/);
  });

  it('adds the seven focused Founder actions above the native composer', () => {
    const source = read(
      'src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/SidebarChat.tsx',
    );
    for (const action of ['Verify', 'Challenge', 'Research', 'Panel', 'Test', 'Explain', 'Optimize']) {
      assert.match(source, new RegExp(`label: '${action}'`));
    }
    assert.match(source, /aria-label='Founder actions'/);
  });
});

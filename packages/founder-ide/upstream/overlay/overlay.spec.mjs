import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const read = (relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');

describe('Founder IDE upstream overlay', () => {
  it('ships the native composer override through the manifest', () => {
    const manifest = JSON.parse(read('MANIFEST.json'));
    assert.ok(
      manifest.files.some(
        (entry) =>
          entry.dest ===
          'src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/SidebarChat.tsx',
      ),
    );
  });

  it('uses Founder Gateway only for Founder-managed aliases', () => {
    const source = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendLLMMessage.ts',
    );
    assert.match(source, /modelName\.startsWith\('founder-os-'\)/);
    assert.match(source, /founderOsEnabled\(\) && isFounderManagedSelection/);
  });

  it('adds route receipts and Founder identity to managed responses', () => {
    const source = read(
      'src/vs/workbench/contrib/void/electron-main/llmMessage/sendFounderOs.ts',
    );
    assert.match(source, /You are Founder AI/);
    assert.match(source, /\*\*Founder route\*\*/);
    assert.match(source, /founder_os_metadata: true/);
  });

  it('keeps voice input and personal AI next to the native composer', () => {
    const source = read(
      'src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/SidebarChat.tsx',
    );
    assert.match(source, /Add or manage personal AI/);
    assert.match(source, /Start voice input/);
    assert.match(source, /founderSpeechRecognitionConstructor/);
  });
});

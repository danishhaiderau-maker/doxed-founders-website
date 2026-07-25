import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import {
  FounderCodeIntelligenceIndex,
  discoverWorkspaceFiles,
  workspaceIndexId,
} from '../src/code-intelligence-index';
import { FounderCodeIntelligenceMcpProtocol } from '../src/code-intelligence-mcp';

const cleanup: string[] = [];

afterEach(() => {
  delete process.env.FOUNDER_CODE_INTELLIGENCE_DIR;
  for (const directory of cleanup.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

function fixture(): { root: string; cache: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-code-index-workspace-'));
  const cache = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-code-index-cache-'));
  cleanup.push(root, cache);
  process.env.FOUNDER_CODE_INTELLIGENCE_DIR = cache;
  fs.mkdirSync(path.join(root, 'src'), { recursive: true });
  fs.mkdirSync(path.join(root, 'node_modules', 'ignored'), { recursive: true });
  fs.mkdirSync(path.join(root, 'node_modules-preserved-cache', 'ignored'), { recursive: true });
  fs.writeFileSync(
    path.join(root, 'src', 'api.ts'),
    "export function requestFounderApi() { return 'ok'; }\n",
  );
  fs.writeFileSync(
    path.join(root, 'src', 'page.ts'),
    "import { requestFounderApi } from './api';\nexport const Page = requestFounderApi;\n",
  );
  fs.writeFileSync(path.join(root, '.env'), 'PLATFORM_KEY=never-index-this\n');
  fs.writeFileSync(path.join(root, 'node_modules', 'ignored', 'secret.ts'), 'do not scan\n');
  fs.writeFileSync(
    path.join(root, 'node_modules-preserved-cache', 'ignored', 'duplicate.ts'),
    'do not scan preserved caches\n',
  );
  return { root, cache };
}

describe('Founder Node code intelligence index', () => {
  it('discovers source while excluding generated folders and credential files', () => {
    const { root } = fixture();
    assert.deepEqual(discoverWorkspaceFiles(root), ['src/api.ts', 'src/page.ts']);
  });

  it('reuses unchanged hashes, refreshes one changed file, and survives restart', async () => {
    const { root } = fixture();
    const index = new FounderCodeIntelligenceIndex(root);
    const first = await index.refresh();
    assert.equal(first.files, 2);
    assert.equal(first.refreshedFiles, 2);
    assert.equal(first.reusedFiles, 0);
    assert.equal(fs.existsSync(first.persistedAt), true);

    const warmQuery = await index.query({ query: 'request founder api' });
    assert.equal(warmQuery.indexedAt, first.indexedAt);

    const second = await index.refresh();
    assert.equal(second.refreshedFiles, 0);
    assert.equal(second.reusedFiles, 2);

    fs.writeFileSync(
      path.join(root, 'src', 'api.ts'),
      "export function requestFounderApi() { return 'changed and longer'; }\n",
    );
    const third = await index.refresh();
    assert.equal(third.refreshedFiles, 1);
    assert.equal(third.reusedFiles, 1);

    const restarted = new FounderCodeIntelligenceIndex(root);
    const afterRestart = await restarted.refresh();
    assert.equal(afterRestart.refreshedFiles, 0);
    assert.equal(afterRestart.reusedFiles, 2);
    assert.equal(afterRestart.workspaceId, first.workspaceId);
  });

  it('returns bounded graph tuples without exposing source or secrets', async () => {
    const { root } = fixture();
    const index = new FounderCodeIntelligenceIndex(root);
    const result = await index.query({
      query: 'request founder api',
      activeFile: 'src/page.ts',
      maxEstimatedTokens: 700,
    });
    assert.equal(result.tuples[0]?.file, 'src/api.ts');
    assert.ok(result.tuples.some((tuple) => tuple.file === 'src/page.ts'));
    assert.ok(result.tuples.some((tuple) => tuple.file === 'src/api.ts'));
    assert.match(result.promptMap, /src\/api\.ts/);
    assert.ok(result.promptMap.length <= 2_800);
    assert.doesNotMatch(result.promptMap, /PLATFORM_KEY|never-index-this/);
    assert.equal(await index.query({
      query: 'request founder api',
      activeFile: 'src/page.ts',
      maxEstimatedTokens: 700,
    }), result);
  });

  it('continues in memory when the preferred cache cannot be written', async () => {
    const { root, cache } = fixture();
    const blockedCache = path.join(cache, 'not-a-directory');
    fs.writeFileSync(blockedCache, 'block directory creation');
    process.env.FOUNDER_CODE_INTELLIGENCE_DIR = blockedCache;

    const result = await new FounderCodeIntelligenceIndex(root).query({
      query: 'request founder api',
    });

    assert.equal(result.persistence, 'memory');
    assert.ok(result.tuples.some((tuple) => tuple.file === 'src/api.ts'));
  });

  it('isolates different workspace roots', () => {
    const first = fixture();
    const second = fixture();
    assert.notEqual(workspaceIndexId(first.root), workspaceIndexId(second.root));
  });

  it('speaks the local MCP initialize, tools/list, and tools/call contract', async () => {
    const { root } = fixture();
    const protocol = new FounderCodeIntelligenceMcpProtocol(root);
    const initialized = await protocol.handle({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: { protocolVersion: '2025-06-18' },
    });
    assert.equal(
      (initialized?.result as { protocolVersion?: string }).protocolVersion,
      '2025-06-18',
    );
    const listed = await protocol.handle({
      jsonrpc: '2.0',
      id: 2,
      method: 'tools/list',
    });
    assert.deepEqual(
      (listed?.result as { tools: Array<{ name: string }> }).tools.map((tool) => tool.name),
      [
        'founder_code_map',
        'founder_dependency_impact',
        'founder_refresh_code_index',
      ],
    );
    const called = await protocol.handle({
      jsonrpc: '2.0',
      id: 3,
      method: 'tools/call',
      params: {
        name: 'founder_code_map',
        arguments: { query: 'founder api', activeFile: 'src/page.ts' },
      },
    });
    const payload = (called?.result as {
      structuredContent: { tuples: Array<{ file: string }> };
    }).structuredContent;
    assert.ok(payload.tuples.some((tuple) => tuple.file === 'src/api.ts'));
    assert.equal(
      await protocol.handle({
        jsonrpc: '2.0',
        method: 'notifications/initialized',
      }),
      null,
    );
  });
});

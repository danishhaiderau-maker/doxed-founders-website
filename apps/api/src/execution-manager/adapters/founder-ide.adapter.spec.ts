import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { FounderIdeAdapter } from './founder-ide.adapter';

describe('Founder IDE process-global adapter', () => {
  it('never claims a user-scoped IDE is globally connected', async () => {
    const adapter = new FounderIdeAdapter();
    await adapter.connect();
    assert.equal(adapter.isConnected(), false);
  });

  it('never falls back to the API host filesystem or shell', async () => {
    const adapter = new FounderIdeAdapter();
    assert.deepEqual(await adapter.readWorkspace('.'), []);
    assert.deepEqual(
      await adapter.applyEdits([
        { path: 'src/app.ts', kind: 'overwrite', content: 'unsafe' },
      ]),
      [
        {
          path: 'src/app.ts',
          ok: false,
          error: 'user_scoped_ide_bridge_required',
        },
      ],
    );
    const command = await adapter.runCommand('echo should-not-run');
    assert.equal(command.exitCode, 126);
    assert.equal(command.stderr, 'user_scoped_ide_bridge_required');
  });
});

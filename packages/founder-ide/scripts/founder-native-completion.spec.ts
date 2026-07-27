import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  evaluateNativeFounderCompletion,
  founderNativeCompletionReceipt,
  isNativeFounderVisualVerificationCommand,
  type FounderNativeEvidenceMessage,
} from '../upstream/overlay/src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeCompletion';

function editTurn(path: string, result = 'Applied edit successfully.'): FounderNativeEvidenceMessage[] {
  return [
    {
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: 'edit-1',
        function: { name: 'edit_file', arguments: JSON.stringify({ uri: path }) },
      }],
    },
    { role: 'tool', tool_call_id: 'edit-1', content: result },
  ];
}

function commandTurn(command: string, result = '[exit code 0]'): FounderNativeEvidenceMessage[] {
  return [
    {
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: `command-${command}`,
        function: { name: 'run_command', arguments: JSON.stringify({ command }) },
      }],
    },
    { role: 'tool', tool_call_id: `command-${command}`, content: result },
  ];
}

describe('Founder native completion evidence', () => {
  it('accepts a read-only answer and rejects Plan-mode mutation', () => {
    const answer = evaluateNativeFounderCompletion({
      messages: [{ role: 'user', content: 'Explain routing.' }],
      mode: 'ask',
      goal: 'Explain routing.',
      finalAnswer: 'Founder Auto chooses the eligible route.',
      requestCompleted: true,
    });
    const mutation = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Plan the change.' },
        ...editTurn('src/app.ts'),
      ],
      mode: 'plan',
      goal: 'Plan the change.',
      finalAnswer: 'Plan ready.',
      requestCompleted: true,
    });

    assert.equal(answer.verdict, 'passed');
    assert.equal(mutation.verdict, 'incomplete');
    assert.match(mutation.missing.join(' '), /Plan mode changed/);
  });

  it('requires an observed edit and passing command for implementation', () => {
    const noEdit = evaluateNativeFounderCompletion({
      messages: [{ role: 'user', content: 'Fix the gateway.' }],
      mode: 'build',
      goal: 'Fix the gateway.',
      finalAnswer: 'Done.',
      requestCompleted: true,
    });
    const noCheck = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Fix the gateway.' },
        ...editTurn('apps/api/src/gateway.ts'),
      ],
      mode: 'build',
      goal: 'Fix the gateway.',
      finalAnswer: 'Done.',
      requestCompleted: true,
    });

    assert.match(noEdit.missing.join(' '), /no workspace edit/);
    assert.match(noCheck.missing.join(' '), /no passing test/);
  });

  it('accepts verified code and requires visual proof for UI changes', () => {
    const code = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Fix the gateway.' },
        ...editTurn('apps/api/src/gateway.ts'),
        ...commandTurn('npm test -- gateway'),
      ],
      mode: 'debug',
      goal: 'Fix the gateway.',
      finalAnswer: 'The gateway is fixed and tested.',
      requestCompleted: true,
    });
    const ui = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Update the navigation.' },
        ...editTurn('apps/web/src/components/nav.tsx'),
        ...commandTurn('npm run typecheck'),
      ],
      mode: 'build',
      goal: 'Update the navigation.',
      finalAnswer: 'The navigation is updated.',
      requestCompleted: true,
    });
    const visual = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Update the navigation.' },
        ...editTurn('apps/web/src/components/nav.tsx'),
        ...commandTurn('npm run typecheck'),
        ...commandTurn('npm run test:playwright -- nav'),
      ],
      mode: 'build',
      goal: 'Update the navigation.',
      finalAnswer: 'The navigation is updated.',
      requestCompleted: true,
    });

    assert.equal(code.verdict, 'passed');
    assert.equal(ui.verdict, 'incomplete');
    assert.match(ui.missing.join(' '), /no passing visual/);
    assert.equal(visual.verdict, 'passed');
    assert.match(founderNativeCompletionReceipt(visual), /Passed \| 1 file \| 2 checks/);
  });

  it('requires a dedicated visual runner rather than a visual keyword', () => {
    assert.equal(isNativeFounderVisualVerificationCommand('echo screenshot'), false);
    assert.equal(
      isNativeFounderVisualVerificationCommand('npm run typecheck -- screenshot'),
      false,
    );
    assert.equal(
      isNativeFounderVisualVerificationCommand('npm run test:playwright && echo passed'),
      false,
    );
    assert.equal(
      isNativeFounderVisualVerificationCommand('npm run test:playwright -- navigation'),
      true,
    );
    assert.equal(
      isNativeFounderVisualVerificationCommand(
        'node packages/founder-ide/scripts/installed-founder-navigation-qa.mjs',
      ),
      true,
    );
  });

  it('ignores earlier turns and failed tool results', () => {
    const receipt = evaluateNativeFounderCompletion({
      messages: [
        { role: 'user', content: 'Old task.' },
        ...editTurn('old.ts'),
        ...commandTurn('npm test'),
        { role: 'user', content: 'Fix the new task.' },
        ...editTurn('new.ts', 'Error: edit failed'),
        ...commandTurn('npm test', '[exit code 1]'),
      ],
      mode: 'build',
      goal: 'Fix the new task.',
      finalAnswer: 'Done.',
      requestCompleted: true,
    });

    assert.equal(receipt.verdict, 'incomplete');
    assert.equal(receipt.editedFiles.length, 0);
    assert.equal(receipt.passedChecks.length, 0);
  });
});

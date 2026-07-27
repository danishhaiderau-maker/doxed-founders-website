import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildFounderSecondBrainPrompt,
  buildFounderSecondBrainReconciliationPrompt,
  founderSecondBrainIntents,
} from '../upstream/overlay/src/vs/workbench/contrib/void/browser/react/src/sidebar-tsx/founderSecondBrain';
import {
  buildFounderReviewEvidencePack,
  founderReviewEvidenceMessage,
  founderReviewChatMode,
  founderReviewTools,
  isFounderIndependentReview,
  isFounderReviewReconciliation,
  renderFounderIndependentReview,
} from '../upstream/overlay/src/vs/workbench/contrib/void/electron-main/llmMessage/founderIndependentReview';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

describe('Founder Second brain boundary', () => {
  it('builds a redacted structured evidence packet and result contract', () => {
    const prompt = buildFounderSecondBrainPrompt(
      [
        { role: 'user', content: 'Fix login. api_key=do-not-leak' },
        { role: 'assistant', displayContent: 'Implemented login and tests.' },
      ],
      'qa',
      'GLM reviewer',
    );
    assert.match(prompt, /^\[FOUNDER_SECOND_BRAIN_V1\]/);
    assert.match(prompt, /founder_second_brain_context/);
    assert.match(prompt, /"allow_workspace_mutation":false/);
    assert.match(prompt, /"verdict":"pass \| needs_correction \| insufficient_evidence"/);
    assert.doesNotMatch(prompt, /do-not-leak/);
  });

  it('offers read-only workspace housekeeping as an independent opinion', () => {
    const housekeeping = founderSecondBrainIntents.find(
      intent => intent.id === 'housekeeping',
    );
    assert.equal(housekeeping?.label, 'Check workspace health');
    const prompt = buildFounderSecondBrainPrompt(
      [
        { role: 'user', content: 'Keep this workspace efficient.' },
        { role: 'assistant', displayContent: 'The implementation is complete.' },
      ],
      'housekeeping',
      'Kimi reviewer',
    );
    assert.match(prompt, /obsolete duplicate source trees/);
    assert.match(prompt, /Do not delete, move, edit, or commit anything/);
    assert.match(prompt, /"allow_workspace_mutation":false/);
  });

  it('recognizes only the latest founder review request', () => {
    assert.equal(
      isFounderIndependentReview([
        { role: 'user', content: '[FOUNDER_SECOND_BRAIN_V1]\n{}' },
        { role: 'assistant', content: '{}' },
      ]),
      true,
    );
    assert.equal(
      isFounderIndependentReview([
        { role: 'user', content: '[FOUNDER_SECOND_BRAIN_V1]\n{}' },
        { role: 'assistant', content: '{}' },
        { role: 'user', content: 'Now edit the file.' },
      ]),
      false,
    );
  });

  it('builds and recognizes a bounded read-only reconciliation request', () => {
    const prompt = buildFounderSecondBrainReconciliationPrompt(
      [
        { role: 'user', content: 'Fix login.' },
        { role: 'assistant', displayContent: 'Login is fixed.' },
      ],
      'The reviewer found a missing expiry check.',
      'audit',
      'GLM reviewer',
    );
    assert.match(prompt, /^\[FOUNDER_SECOND_BRAIN_RECONCILE_V1\]/);
    assert.match(prompt, /Accepted verified findings/);
    assert.match(prompt, /Approval required/);
    assert.equal(
      isFounderReviewReconciliation([{ role: 'user', content: prompt }]),
      true,
    );
  });

  it('forces inspection mode and removes mutating or ambiguous tools', () => {
    const tools = founderReviewTools(true, [
      { name: 'read_file' },
      { name: 'codebase_search' },
      { name: 'git_diff' },
      { name: 'run_command' },
      { name: 'edit_file' },
      { name: 'custom_magic' },
    ]);
    assert.deepEqual(tools?.map(tool => tool.name), [
      'read_file',
      'codebase_search',
      'git_diff',
    ]);
    assert.equal(founderReviewChatMode(true, 'agent'), 'gather');
  });

  it('assembles an allowlisted project, decision, task, and diff snapshot', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-review-'));
    fs.mkdirSync(path.join(root, '.github', 'founder-os'), { recursive: true });
    fs.writeFileSync(
      path.join(root, '.github', 'founder-os', 'project-context.md'),
      'Project graph\napi_key=do-not-leak',
    );
    fs.writeFileSync(
      path.join(root, '.github', 'founder-os', 'decisions.md'),
      'Use one editing owner.',
    );
    fs.writeFileSync(
      path.join(root, '.github', 'founder-os', 'tasks.json'),
      '{"goal":"ship"}',
    );
    const pack = buildFounderReviewEvidencePack(root, args => {
      if (args[0] === 'rev-parse') return 'abc123\n';
      if (args[0] === 'status') return ' M src/login.ts\n?? tests/login.spec.ts\n';
      if (args[0] === 'diff') return ' src/login.ts | 2 +-\n';
      return '';
    });
    assert.equal(pack?.head, 'abc123');
    assert.deepEqual(pack?.changed_files, [
      'M src/login.ts',
      '?? tests/login.spec.ts',
    ]);
    assert.match(pack?.project_context ?? '', /api_key=\[REDACTED\]/);
    assert.match(founderReviewEvidenceMessage(pack), /founder_second_brain_workspace_evidence/);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('renders a valid structured verdict as concise founder-facing evidence', () => {
    const rendered = renderFounderIndependentReview(JSON.stringify({
      schema_version: 1,
      verdict: 'needs_correction',
      summary: 'The login path is not complete.',
      verified_defects: [{
        severity: 'high',
        finding: 'Expired sessions are accepted.',
        evidence_refs: ['auth.ts:42', 'session.spec.ts'],
        correction: 'Reject expired sessions before minting a token.',
      }],
      opinions: ['A smaller surface would be easier to maintain.'],
      better_option: 'Keep one session validator.',
      competition: {
        differentiated: [],
        commodity: [],
        competitor_better: [],
        primary_sources: [],
      },
      required_tests: ['Expired session integration test'],
      inspected_evidence: ['auth.ts', 'session.spec.ts'],
      residual_risks: ['Clock skew'],
      confidence: 87,
    }));
    assert.equal(rendered.valid, true);
    assert.match(rendered.text, /Second brain: Needs correction/);
    assert.match(rendered.text, /\*\*HIGH\*\* Expired sessions are accepted/);
    assert.match(rendered.text, /Confidence:\*\* 87%/);
    assert.match(rendered.text, /No files, deployments, approvals, or credentials were changed/);
  });

  it('fails closed when a reviewer returns unstructured prose', () => {
    const rendered = renderFounderIndependentReview('Everything looks good to me.');
    assert.equal(rendered.valid, false);
    assert.match(rendered.text, /Insufficient evidence/);
    assert.match(rendered.text, /did not accept a pass verdict/);
  });
});

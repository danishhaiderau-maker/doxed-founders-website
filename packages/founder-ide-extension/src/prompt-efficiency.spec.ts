import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  composeFounderSystemPrompt,
  composeFounderPromptMessages,
  estimateMessagesTokens,
  planPromptEfficiency,
  type EfficientPromptMessage,
} from './prompt-efficiency';
import { runFounderEfficiencyBenchmark } from './prompt-efficiency-benchmark';

describe('Founder prompt efficiency', () => {
  it('keeps the stable identity block first for provider prefix caching', () => {
    const prompt = composeFounderSystemPrompt({
      identity: 'stable identity',
      memory: 'founder memory',
      projectContext: 'project map',
      coordination: 'coordination',
    });
    assert.equal(
      prompt,
      'stable identity\n\nfounder memory\n\nproject map\n\ncoordination',
    );
  });

  it('keeps the first provider message byte-stable when live context changes', () => {
    const first = composeFounderPromptMessages({
      identity: 'Founder identity v1',
      memory: 'memory one',
      projectContext: 'repo map one',
    });
    const second = composeFounderPromptMessages({
      identity: 'Founder identity v1',
      memory: 'memory two',
      projectContext: 'repo map two',
      coordination: 'agent heartbeat',
    });
    assert.deepEqual(first[0], second[0]);
    assert.notEqual(first[1]?.content, second[1]?.content);
  });

  it('keeps only the latest live coordination snapshot', () => {
    const messages: EfficientPromptMessage[] = [
      { role: 'system', content: 'identity' },
      { role: 'system', content: '## Live agent coordination\nold' },
      { role: 'system', content: '## Live agent coordination\nlatest' },
      { role: 'user', content: 'continue' },
    ];
    const plan = planPromptEfficiency(messages);
    assert.equal(plan.estimate.removedStaleCoordinationBlocks, 1);
    assert.equal(plan.messages.some((message) => message.content.endsWith('old')), false);
    assert.equal(plan.messages.some((message) => message.content.endsWith('latest')), true);
  });

  it('bounds tool output while preserving its beginning, end, and content hash', () => {
    const content = `BEGIN-${'x'.repeat(30_000)}-END`;
    const plan = planPromptEfficiency([
      { role: 'tool', content, name: 'founder-run-command', tool_call_id: '1' },
    ], { maxToolResultChars: 4_000 });
    const compacted = plan.messages[0]!.content;
    assert.ok(compacted.length <= 4_000);
    assert.match(compacted, /^BEGIN-/);
    assert.match(compacted, /-END$/);
    assert.match(compacted, /full output remains in the local terminal; sha256:/);
    assert.equal(plan.estimate.compactedToolResults, 1);
    assert.ok(plan.estimate.avoidedTokens > 0);
  });

  it('is deterministic across the required five same-input benchmark runs', () => {
    const fixtures = Array.from({ length: 50 }, (_, index): EfficientPromptMessage[] => [
      { role: 'system', content: 'Founder stable identity and tool contract' },
      { role: 'user', content: `Task ${index}: inspect, edit, test, and report evidence.` },
      {
        role: 'tool',
        name: 'founder-run-command',
        tool_call_id: String(index),
        content: `test output ${index}\n${'fixture line\n'.repeat(2_000)}done`,
      },
    ]);
    const runs = Array.from({ length: 5 }, () =>
      fixtures.map((fixture) => planPromptEfficiency(fixture).estimate),
    );
    assert.deepEqual(runs[0], runs[1]);
    assert.deepEqual(runs[0], runs[4]);
    assert.equal(runs[0]!.length, 50);
    assert.ok(runs[0]!.every((estimate) => estimate.measurement === 'estimated'));
    assert.ok(runs[0]!.every((estimate) => estimate.sentTokens < estimate.baselineTokens));
  });

  it('does not mutate messages or invent savings for an already compact prompt', () => {
    const messages: EfficientPromptMessage[] = [{ role: 'user', content: 'hello' }];
    const before = JSON.stringify(messages);
    const plan = planPromptEfficiency(messages);
    assert.equal(JSON.stringify(messages), before);
    assert.equal(plan.estimate.avoidedTokens, 0);
    assert.equal(plan.estimate.sentTokens, estimateMessagesTokens(messages));
  });

  it('benchmarks five named coding workloads against the full-context baseline', () => {
    const first = runFounderEfficiencyBenchmark();
    const repeated = Array.from({ length: 4 }, () => runFounderEfficiencyBenchmark());
    assert.equal(first.length, 5);
    for (const run of repeated) assert.deepEqual(run, first);
    assert.ok(first.every((result) => result.baselineTokens > result.sentTokens));
    assert.ok(first.every((result) => result.avoidedTokens === result.baselineTokens - result.sentTokens));
  });
});

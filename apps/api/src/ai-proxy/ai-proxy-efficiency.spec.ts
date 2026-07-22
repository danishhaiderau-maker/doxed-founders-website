import assert from 'node:assert/strict';
import test from 'node:test';
import {
  clientIncludedFounderMemory,
  parseClientPromptEfficiency,
} from './ai-proxy-efficiency';

test('accepts only explicit client memory inclusion', () => {
  assert.equal(clientIncludedFounderMemory({ founder_memory_included: true }), true);
  assert.equal(clientIncludedFounderMemory({ founder_memory_included: 'true' }), false);
  assert.equal(clientIncludedFounderMemory(null), false);
});

test('sanitizes a consistent estimated efficiency receipt', () => {
  assert.deepEqual(
    parseClientPromptEfficiency({
      prompt_efficiency: {
        measurement: 'estimated',
        baselineTokens: 1_000,
        sentTokens: 600,
        avoidedTokens: 400,
        savingsPercent: 99,
        compactedToolResults: 2,
        removedStaleCoordinationBlocks: 1,
        techniques: ['bounded-tool-results', 'x'.repeat(100)],
      },
    }),
    {
      measurement: 'estimated',
      baselineTokens: 1_000,
      sentTokens: 600,
      avoidedTokens: 400,
      savingsPercent: 40,
      compactedToolResults: 2,
      removedStaleCoordinationBlocks: 1,
      techniques: ['bounded-tool-results', 'x'.repeat(48)],
    },
  );
});

test('rejects contradictory or unbounded efficiency claims', () => {
  assert.equal(parseClientPromptEfficiency({
    prompt_efficiency: {
      measurement: 'estimated',
      baselineTokens: 100,
      sentTokens: 80,
      avoidedTokens: 99,
      compactedToolResults: 0,
      removedStaleCoordinationBlocks: 0,
      techniques: [],
    },
  }), null);
  assert.equal(parseClientPromptEfficiency({
    prompt_efficiency: {
      measurement: 'measured',
      baselineTokens: 100,
      sentTokens: 80,
      avoidedTokens: 20,
      compactedToolResults: 0,
      removedStaleCoordinationBlocks: 0,
      techniques: [],
    },
  }), null);
});

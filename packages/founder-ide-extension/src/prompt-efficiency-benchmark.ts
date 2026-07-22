import { planPromptEfficiency, type EfficientPromptMessage } from './prompt-efficiency';

export interface FounderEfficiencyBenchmarkResult {
  fixture: string;
  baselineTokens: number;
  sentTokens: number;
  avoidedTokens: number;
  savingsPercent: number;
}

const FIXTURES: ReadonlyArray<{ name: string; messages: EfficientPromptMessage[] }> = [
  {
    name: 'gateway-stream-debug',
    messages: task('Fix the authenticated SSE parser and verify DONE handling.', 36_000),
  },
  {
    name: 'settings-provider-profile',
    messages: task('Add an OpenAI-compatible personal AI profile to Founder Settings.', 24_000),
  },
  {
    name: 'remote-edit-safety',
    messages: task('Review remote proposed-edit approval boundaries and tests.', 42_000),
  },
  {
    name: 'workspace-impact-review',
    messages: task('Find dependants of a shared workspace context module before editing.', 18_000),
  },
  {
    name: 'installer-release-check',
    messages: task('Diagnose a Windows installer build failure and retain the useful log tail.', 54_000),
  },
];

export function runFounderEfficiencyBenchmark(): FounderEfficiencyBenchmarkResult[] {
  return FIXTURES.map((fixture) => {
    const estimate = planPromptEfficiency(fixture.messages).estimate;
    return {
      fixture: fixture.name,
      baselineTokens: estimate.baselineTokens,
      sentTokens: estimate.sentTokens,
      avoidedTokens: estimate.avoidedTokens,
      savingsPercent: estimate.savingsPercent,
    };
  });
}

function task(goal: string, outputCharacters: number): EfficientPromptMessage[] {
  return [
    { role: 'system', content: 'Founder identity v1' },
    { role: 'system', content: '## Live agent coordination\nEarlier stale task ownership.' },
    { role: 'system', content: '## Live agent coordination\nCurrent fenced task ownership.' },
    { role: 'user', content: goal },
    {
      role: 'tool',
      name: 'founder-run-command',
      tool_call_id: 'benchmark-tool',
      content: `BEGIN ${goal}\n${'diagnostic line with stable context\n'.repeat(Math.ceil(outputCharacters / 36))}END`,
    },
  ];
}

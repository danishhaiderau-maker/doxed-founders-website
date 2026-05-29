import { buildSuggestionFromBuildPrompt } from './cursor-build-room';

export const COMMAND_BAR_CREDITS = 10;

export type CommandBarIntent = 'roadmap' | 'release_notes' | 'weekly_summary';

export type QuickBuildResult = {
  ideaTitle: string;
  spec: string;
  tasks: string[];
  githubIssues: string[];
  roadmapTitle: string;
  cursorPrompt: string;
  traderView: string;
};

export type CommandBarResult = {
  title: string;
  summary: string;
  body: string;
  queueItems: { kind: 'SPEC' | 'TASK' | 'ROADMAP' | 'GITHUB_ISSUE'; title: string; description?: string }[];
  cursorPrompt: string;
};

function splitPromptLines(prompt: string): string[] {
  return prompt
    .trim()
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);
}

/** Mobile/voice capture → structured build queue (rule-based, no LLM). */
export function processQuickBuild(prompt: string, projectName?: string): QuickBuildResult {
  const name = projectName ?? 'your project';
  const lines = splitPromptLines(prompt);
  const goal = lines[0] ?? prompt.trim();
  const built = buildSuggestionFromBuildPrompt(prompt);

  const tasks =
    lines.length > 1
      ? lines.slice(1)
      : [
          `Define scope for: ${goal}`,
          'Add acceptance criteria',
          'Wire into Founder OS publish flow',
          'Sync GitHub when shipped',
        ];

  const githubIssues = [
    `[Feature] ${goal.slice(0, 72)}`,
    ...tasks.slice(0, 2).map((t) => `[Task] ${t.slice(0, 60)}`),
  ];

  const spec = [
    `# Spec: ${goal}`,
    '',
    `Project: ${name}`,
    '',
    '## Goal',
    goal,
    '',
    '## Tasks',
    ...tasks.map((t) => `- ${t}`),
    '',
    '## GitHub issues (draft)',
    ...githubIssues.map((i) => `- ${i}`),
    '',
    '## Next',
    'Open Cursor at desk · paste cursor prompt · execute · publish everywhere.',
  ].join('\n');

  const cursorPrompt = [
    `Implement for ${name}:`,
    '',
    goal,
    '',
    'Tasks:',
    ...tasks.map((t) => `- ${t}`),
    '',
    'Acceptance: ship minimal working version, update Founder OS build feed when done.',
  ].join('\n');

  return {
    ideaTitle: goal.slice(0, 120),
    spec,
    tasks,
    githubIssues,
    roadmapTitle: goal.slice(0, 80),
    cursorPrompt,
    traderView: built.traderSummary,
  };
}

export function processCommandBar(
  intent: CommandBarIntent,
  prompt: string,
  context?: { projectName?: string; recentHeadlines?: string[] },
): CommandBarResult {
  const name = context?.projectName ?? 'your project';
  const topic = prompt.trim() || name;
  const headlines = context?.recentHeadlines ?? [];

  switch (intent) {
    case 'roadmap': {
      const phases = [
        `Phase 1 — Foundation: ${topic}`,
        'Phase 2 — MVP core flows',
        'Phase 3 — Community beta + demand validation',
        'Phase 4 — Launch readiness',
      ];
      return {
        title: `Roadmap: ${topic.slice(0, 60)}`,
        summary: `Quarterly roadmap draft for ${name}.`,
        body: phases.map((p, i) => `${i + 1}. ${p}`).join('\n'),
        queueItems: phases.map((p) => ({ kind: 'ROADMAP' as const, title: p })),
        cursorPrompt: [
          `Create a product roadmap for ${name} focused on: ${topic}`,
          '',
          ...phases.map((p) => `- ${p}`),
        ].join('\n'),
      };
    }
    case 'release_notes': {
      const items =
        headlines.length > 0
          ? headlines.slice(0, 5)
          : ['Mobile build queue', 'Agent marketplace hooks', 'Publish everywhere flow'];
      const body = items.map((h) => `• ${h}`).join('\n');
      return {
        title: `Release notes — ${name}`,
        summary: 'Draft release notes from recent build activity.',
        body: [`What's new in ${name}:`, '', body, '', 'Built in public on Founder OS.'].join('\n'),
        queueItems: [{ kind: 'SPEC', title: 'Release notes draft', description: body }],
        cursorPrompt: `Write release notes for ${name}:\n${body}`,
      };
    }
    case 'weekly_summary':
    default: {
      const highlights =
        headlines.length > 0
          ? headlines
          : ['Captured ideas in build queue', 'Synced community updates', 'Planned next sprint'];
      const body = highlights.map((h) => `• ${h}`).join('\n');
      return {
        title: `Weekly summary — ${name}`,
        summary: 'Progress recap for founders and community.',
        body: [`This week on ${name}:`, '', body, '', 'Next: pick top queue item and ship.'].join('\n'),
        queueItems: highlights.slice(0, 3).map((h) => ({
          kind: 'TASK' as const,
          title: `Follow-up: ${h.slice(0, 72)}`,
        })),
        cursorPrompt: `Summarize this week's progress for ${name}:\n${body}`,
      };
    }
  }
}

export function buildCursorCopyBlock(items: {
  title: string;
  spec?: string | null;
  cursorPrompt?: string | null;
  githubIssues?: { title: string }[];
}): string {
  const parts = [
    '# Founder OS → Cursor',
    '',
    `## ${items.title}`,
    '',
  ];
  if (items.spec) {
    parts.push(items.spec, '', '---', '');
  }
  if (items.githubIssues?.length) {
    parts.push('## GitHub issues to create', ...items.githubIssues.map((i) => `- ${i.title}`), '', '---', '');
  }
  if (items.cursorPrompt) {
    parts.push('## Paste into Cursor', '', items.cursorPrompt);
  }
  return parts.join('\n');
}

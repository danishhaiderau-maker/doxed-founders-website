import { buildSuggestionFromBuildPrompt } from './cursor-build-room';

export const AGENT_RUN_CREDITS = 15;

export const AGENT_CATEGORY_LABELS: Record<string, string> = {
  RESEARCH: 'Research',
  TRADING: 'Trading',
  COMMUNITY: 'Community',
  SUPPORT: 'Customer support',
  MARKETING: 'Marketing',
  TOKENOMICS: 'Tokenomics',
  AUDIT: 'Audit',
  BUILDER: 'Builder',
  FUNDRAISING: 'Fundraising',
  LAUNCH: 'Launch',
};

export const WORKFORCE_TEMPLATES: {
  key: string;
  label: string;
  category: string;
  description: string;
}[] = [
  { key: 'PRODUCT_MANAGER', label: 'Product Manager', category: 'BUILDER', description: 'Specs, roadmap, and task breakdown' },
  { key: 'RESEARCHER', label: 'Researcher', category: 'RESEARCH', description: 'Competitor and market research briefs' },
  { key: 'BUILDER', label: 'Builder', category: 'BUILDER', description: 'Code plans and GitHub-ready tasks' },
  { key: 'MARKETER', label: 'Marketer', category: 'MARKETING', description: 'Posts, threads, and launch copy' },
  { key: 'COMMUNITY_MANAGER', label: 'Community Manager', category: 'COMMUNITY', description: 'FAQ replies and community updates' },
  { key: 'FUNDRAISING', label: 'Fundraising Agent', category: 'FUNDRAISING', description: 'Demand signals and raise readiness' },
  { key: 'LAUNCH', label: 'Launch Agent', category: 'LAUNCH', description: 'Launch checklist and readiness score' },
];

export type WorkforceAgentOutput = {
  title: string;
  summary: string;
  tasks: string[];
  githubIssues: string[];
  buildPlan: string[];
  traderView: string;
};

export type WorkforceTool = 'build_queue' | 'github_issues' | 'cursor_agent' | 'community_draft' | 'raise_room';

/** Phase 3 — which tools each hidden worker may execute at runtime. */
export const WORKFORCE_PERMISSIONS: Record<string, WorkforceTool[]> = {
  PRODUCT_MANAGER: ['build_queue', 'github_issues'],
  RESEARCHER: ['build_queue', 'github_issues'],
  BUILDER: ['build_queue', 'github_issues', 'cursor_agent'],
  MARKETER: ['build_queue', 'community_draft'],
  COMMUNITY_MANAGER: ['build_queue', 'community_draft'],
  FUNDRAISING: ['build_queue', 'raise_room', 'github_issues'],
  LAUNCH: ['build_queue', 'github_issues'],
  CUSTOM: ['build_queue', 'github_issues'],
};

export type WorkforceRuntimeResult = {
  toolsUsed: WorkforceTool[];
  permissions: WorkforceTool[];
  githubIssuesCreated: number;
  githubRepo: string | null;
  cursorDispatched: boolean;
  cursorAgentUrl: string | null;
  communityDraftSaved: boolean;
  raiseRoomLinked: boolean;
};

export function emptyWorkforceRuntime(template: string): WorkforceRuntimeResult {
  const permissions = WORKFORCE_PERMISSIONS[template] ?? WORKFORCE_PERMISSIONS.BUILDER;
  return {
    toolsUsed: ['build_queue'],
    permissions,
    githubIssuesCreated: 0,
    githubRepo: null,
    cursorDispatched: false,
    cursorAgentUrl: null,
    communityDraftSaved: false,
    raiseRoomLinked: false,
  };
}

export function formatWorkforceRuntimeSummary(runtime: WorkforceRuntimeResult): string {
  const lines: string[] = ['**Runtime actions**'];
  if (runtime.githubIssuesCreated > 0 && runtime.githubRepo) {
    lines.push(`✓ Created ${runtime.githubIssuesCreated} GitHub issue(s) on \`${runtime.githubRepo}\``);
  } else if (runtime.permissions.includes('github_issues')) {
    lines.push('○ GitHub issues queued locally — connect repo + token to auto-publish');
  }
  if (runtime.cursorDispatched && runtime.cursorAgentUrl) {
    lines.push(`✓ Cursor agent started — ${runtime.cursorAgentUrl}`);
  } else if (runtime.permissions.includes('cursor_agent')) {
    lines.push('○ Connect Cursor in AI Stack to auto-dispatch code tasks');
  }
  if (runtime.communityDraftSaved) {
    lines.push('✓ Community update draft saved — review in Projects');
  }
  if (runtime.raiseRoomLinked) {
    lines.push('✓ Raise Room checklist linked — open Raise Room to review');
  }
  if (lines.length === 1) {
    lines.push('✓ Tasks queued in build queue');
  }
  return lines.join('\n');
}

export function formatAgentRunAnswer(
  agentName: string,
  template: string,
  output: WorkforceAgentOutput,
  answerProvider: 'RULE_BASED' | 'LLM',
  runtime?: WorkforceRuntimeResult,
): string {
  const label = WORKFORCE_TEMPLATES.find((t) => t.key === template)?.label ?? agentName;
  const intent: WorkforceIntent = { template, label, confidence: 'explicit' };
  return formatOrchestratorCopilotAnswer(intent, output, answerProvider, runtime).replace(
    'Routed to your',
    `**${agentName}** ran as your`,
  );
}

export function buildWorkforceGithubContext(input: {
  repoFullName: string | null;
  recentCommits?: { message: string }[];
  openTasks?: string[];
}): string {
  if (!input.repoFullName) return '';
  const lines = [`Connected repo: ${input.repoFullName}`];
  if (input.recentCommits?.length) {
    lines.push(
      'Recent commits:',
      ...input.recentCommits.slice(0, 5).map((c) => `- ${c.message.split('\n')[0].slice(0, 120)}`),
    );
  }
  if (input.openTasks?.length) {
    lines.push('Open tasks:', ...input.openTasks.slice(0, 5).map((t) => `- ${t}`));
  }
  return `\n\n---\nProject context:\n${lines.join('\n')}`;
}

/** Default Copilot prompts when a workforce template card is clicked. */
export const WORKFORCE_COPILOT_PROMPTS: Record<string, string> = {
  PRODUCT_MANAGER:
    'Act as my Product Manager. Break down our MVP into user stories, acceptance criteria, and a prioritized P0/P1 task list for {project}.',
  RESEARCHER:
    'Act as my Researcher. Produce a competitor and market brief for {project} — 5 comparables, pricing models, and community gaps.',
  BUILDER:
    'Act as my Builder agent. Turn our next feature into a code plan with GitHub-ready tasks and a ship sequence for {project}.',
  MARKETER:
    'Act as my Marketer. Draft an X thread, build feed headline, and launch copy for {project}.',
  COMMUNITY_MANAGER:
    'Act as my Community Manager. Draft a welcome message, FAQ for top questions, and a weekly office-hours outline for {project}.',
  FUNDRAISING:
    'Act as my Fundraising agent. Assess raise readiness, demand poll copy, and allocator talking points for {project}.',
  LAUNCH:
    'Act as my Launch agent. Build a go-live checklist with security, deploy verification, and comms timeline for {project}.',
};

export function buildWorkforceCopilotPrompt(templateKey: string, projectName?: string): string {
  const project = projectName?.trim() || 'my project';
  const custom = WORKFORCE_COPILOT_PROMPTS[templateKey];
  if (custom) return custom.replace(/\{project\}/g, project);
  const label = WORKFORCE_TEMPLATES.find((t) => t.key === templateKey)?.label ?? 'Workforce agent';
  return `Act as my ${label}. Help me ship the next milestone for ${project}.`;
}

/** Deep-link into Founder Copilot with a workforce template prompt. */
export function buildCopilotAgentDeepLink(templateKey: string, projectName?: string): string {
  const prompt = buildWorkforceCopilotPrompt(templateKey, projectName);
  const params = new URLSearchParams({
    tab: 'activity',
    agent: templateKey,
    prompt,
  });
  return `/founder-den?${params.toString()}`;
}

export function agentRating(ratingSum: number, ratingCount: number): number {
  if (ratingCount === 0) return 0;
  return Math.round((ratingSum / ratingCount) * 10) / 10;
}

export function runWorkforceAgent(
  template: string,
  prompt: string,
  projectName?: string,
): WorkforceAgentOutput {
  const name = projectName ?? 'your project';
  const lines = prompt.trim().split(/\n+/).filter(Boolean);
  const goal = lines[0] ?? prompt.trim();

  switch (template) {
    case 'PRODUCT_MANAGER':
      return {
        title: `Spec: ${goal.slice(0, 72)}`,
        summary: `Product spec for ${name}. Scope derived from founder prompt — review in Founder Copilot.`,
        tasks: [
          'Define MVP user stories',
          'List acceptance criteria',
          'Prioritize P0 vs P1',
          'Map to Founder OS publish cadence',
        ],
        githubIssues: [`[Spec] ${goal.slice(0, 60)}`, '[Tasks] Break down MVP milestones'],
        buildPlan: ['Week 1: Spec + design', 'Week 2: MVP core', 'Week 3: Community beta'],
        traderView: `Founder is formalizing the roadmap for ${name} — clearer delivery timeline.`,
      };
    case 'RESEARCHER':
      return {
        title: `Research brief: ${goal.slice(0, 72)}`,
        summary: `Competitive landscape and demand signals for ${name}.`,
        tasks: ['Identify 5 comparable products', 'Summarize pricing models', 'Note community gaps'],
        githubIssues: ['[Research] Competitor matrix doc'],
        buildPlan: ['Desk research', 'Community poll', 'Synthesize findings'],
        traderView: 'Team is validating market before heavy build spend.',
      };
    case 'MARKETER':
      return {
        title: `Campaign: ${goal.slice(0, 72)}`,
        summary: `Marketing angles for ${name} — ready to publish everywhere.`,
        tasks: ['Draft X thread (3 tweets)', 'Build feed headline', 'Community announcement'],
        githubIssues: [],
        buildPlan: ['Hook', 'Proof points', 'CTA to project room'],
        traderView: 'Founder sharpening public narrative — visibility increasing.',
      };
    case 'COMMUNITY_MANAGER':
      return {
        title: `Community playbook: ${goal.slice(0, 72)}`,
        summary: `Responses and pinned FAQ for ${name} project room.`,
        tasks: ['Draft welcome message', 'FAQ for top 5 questions', 'Weekly office hours outline'],
        githubIssues: [],
        buildPlan: ['General channel pin', 'Feature request triage', 'Contributor thanks'],
        traderView: 'Community layer getting structured — lower support friction.',
      };
    case 'FUNDRAISING':
      return {
        title: `Demand check: ${goal.slice(0, 72)}`,
        summary: `Simulated raise readiness for ${name}.`,
        tasks: ['Run demand poll copy', 'Allocator talking points', 'Launch readiness gaps'],
        githubIssues: ['[Fundraising] Demand validation checklist'],
        buildPlan: ['Poll → allocate → iterate', 'Track conviction score'],
        traderView: 'Founder testing demand before token narrative hardens.',
      };
    case 'LAUNCH':
      return {
        title: `Launch readiness: ${goal.slice(0, 72)}`,
        summary: `Pre-launch checklist for ${name}.`,
        tasks: ['Security review items', 'Deploy verification', 'Comms timeline'],
        githubIssues: ['[Launch] Go-live checklist'],
        buildPlan: ['T-7 audit', 'T-3 deploy', 'T-0 publish everywhere'],
        traderView: 'Launch discipline visible — reduces last-minute surprises.',
      };
    case 'BUILDER':
    default: {
      const built = buildSuggestionFromBuildPrompt(prompt);
      return {
        title: built.headline,
        summary: built.devSummary,
        tasks: lines.slice(1).length ? lines.slice(1) : [goal],
        githubIssues: [`[Build] ${goal.slice(0, 55)}`],
        buildPlan: ['Open Founder Copilot', 'Sync GitHub', 'Publish everywhere'],
        traderView: built.traderSummary,
      };
    }
  }
}

function extractNumberedLines(text: string, max = 8): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => /^\d+[\).:-]\s+/.test(line))
    .map((line) => line.replace(/^\d+[\).:-]\s+/, '').trim())
    .filter(Boolean)
    .slice(0, max);
}

function extractSection(text: string, heading: string): string | null {
  const re = new RegExp(`##\\s*${heading}\\s*\\n+([\\s\\S]*?)(?=\\n##|$)`, 'i');
  const match = text.match(re);
  return match?.[1]?.trim() ?? null;
}

/** Merge rule-based agent output with an LLM response when a chat key is connected. */
export function mergeWorkforceAgentWithLlm(
  ruleOutput: WorkforceAgentOutput,
  llmText: string,
): WorkforceAgentOutput {
  const trimmed = llmText.trim();
  if (!trimmed) return ruleOutput;

  const titleSection = extractSection(trimmed, 'Title');
  const summarySection = extractSection(trimmed, 'Summary');
  const tasksSection = extractSection(trimmed, 'Tasks');
  const planSection = extractSection(trimmed, 'Build plan') ?? extractSection(trimmed, 'Build Plan');
  const issuesSection = extractSection(trimmed, 'GitHub issues') ?? extractSection(trimmed, 'GitHub Issues');

  const titleFromHeading = trimmed.match(/^#\s+(.+)/m)?.[1]?.trim();
  const tasksFromSection = tasksSection ? extractNumberedLines(tasksSection) : extractNumberedLines(trimmed);
  const planFromSection = planSection ? extractNumberedLines(planSection) : [];
  const issuesFromSection = issuesSection
    ? extractNumberedLines(issuesSection).map((line) => (line.startsWith('[') ? line : `[Build] ${line}`))
    : [];

  return {
    title: (titleSection ?? titleFromHeading ?? ruleOutput.title).slice(0, 120),
    summary: (summarySection ?? trimmed.slice(0, 480)).trim() || ruleOutput.summary,
    tasks: tasksFromSection.length >= 2 ? tasksFromSection : ruleOutput.tasks,
    buildPlan: planFromSection.length >= 2 ? planFromSection : ruleOutput.buildPlan,
    githubIssues: issuesFromSection.length > 0 ? issuesFromSection : ruleOutput.githubIssues,
    traderView: ruleOutput.traderView,
  };
}

export type WorkforceIntent = {
  template: string;
  label: string;
  confidence: 'explicit' | 'high';
};

/** Route Copilot prompts to the right hidden workforce agent (Phase 2 orchestrator). */
export function detectWorkforceIntent(
  prompt: string,
  explicitTemplate?: string | null,
): WorkforceIntent | null {
  const trimmed = prompt.trim();
  if (!trimmed) return null;

  if (explicitTemplate) {
    const match = WORKFORCE_TEMPLATES.find((t) => t.key === explicitTemplate);
    if (match) {
      return { template: match.key, label: match.label, confidence: 'explicit' };
    }
  }

  const lower = trimmed.toLowerCase();

  const fromTemplateDeepLink = WORKFORCE_TEMPLATES.some((t) =>
    lower.includes(`act as my ${t.label.toLowerCase()}`),
  );

  if (!explicitTemplate && !fromTemplateDeepLink) {
    if (
      /^(what|how|why|when|explain|tell me|what's|what is|what are|what changed|what am i|resume|continue where|status|progress|launch readiness)/i.test(
        trimmed,
      ) ||
      /^what should (i work|we build)/i.test(trimmed)
    ) {
      return null;
    }
  }

  for (const t of WORKFORCE_TEMPLATES) {
    const actAs = `act as my ${t.label.toLowerCase()}`;
    if (lower.startsWith(actAs) || lower.includes(`${actAs}.`) || lower.includes(`${actAs},`)) {
      return { template: t.key, label: t.label, confidence: 'explicit' };
    }
  }

  const rules: { template: string; patterns: RegExp[] }[] = [
    {
      template: 'COMMUNITY_MANAGER',
      patterns: [
        /\bcommunity\b/,
        /\bfaq\b/,
        /\bmembers\b/,
        /\bfollowers\b/,
        /\bdiscord\b/,
        /\btelegram\b/,
        /what (is|are) (people|users|community)/,
      ],
    },
    {
      template: 'FUNDRAISING',
      patterns: [
        /\binvestor\b/,
        /\bfundraising\b/,
        /\braise room\b/,
        /\bdeck\b/,
        /\ballocator\b/,
        /\bdemand (poll|signal)/,
        /\bico\b/,
      ],
    },
    {
      template: 'LAUNCH',
      patterns: [/\blaunch readiness\b/, /\bgo-live\b/, /\bgo live\b/, /\bpre-launch\b/, /\bship to prod/],
    },
    {
      template: 'MARKETER',
      patterns: [
        /\btokenomics\b/,
        /\bmarketing\b/,
        /\bx thread\b/,
        /\btweet\b/,
        /\bcampaign\b/,
        /\blaunch copy\b/,
        /\bweekly update\b/,
      ],
    },
    {
      template: 'RESEARCHER',
      patterns: [
        /\bcompetitor\b/,
        /\bmarket research\b/,
        /\bresearch brief\b/,
        /analyze [a-z0-9_-]{2,}/,
        /\bcompare .+ (to|vs|with)/,
        /\blandscape\b/,
      ],
    },
    {
      template: 'PRODUCT_MANAGER',
      patterns: [
        /\broadmap\b/,
        /\buser stor/,
        /\bmvp\b/,
        /\bspec\b/,
        /\bacceptance criteria\b/,
        /\bprioriti/,
        /\bproduct manager\b/,
      ],
    },
    {
      template: 'BUILDER',
      patterns: [
        /\bbuild (the |a |my |wallet|auth|feature|api|mvp)/,
        /\bimplement\b/,
        /\bgithub issue/,
        /\bcode plan\b/,
        /\bwallet auth/,
        /\bfinish (the )?mvp\b/,
        /\bship (this|the|my|a) feature/,
      ],
    },
  ];

  for (const rule of rules) {
    if (rule.patterns.some((re) => re.test(lower))) {
      const label = WORKFORCE_TEMPLATES.find((t) => t.key === rule.template)?.label ?? rule.template;
      return { template: rule.template, label, confidence: 'high' };
    }
  }

  return null;
}

export function formatOrchestratorCopilotAnswer(
  intent: WorkforceIntent,
  output: WorkforceAgentOutput,
  answerProvider: 'RULE_BASED' | 'LLM',
  runtime?: WorkforceRuntimeResult,
): string {
  const providerNote =
    answerProvider === 'LLM' ? ' (via your connected LLM)' : ' (project memory + templates)';
  const taskLines = output.tasks.map((t, i) => `${i + 1}. ${t}`).join('\n');
  const planLines = output.buildPlan.map((s, i) => `${i + 1}. ${s}`).join('\n');
  const issueLines =
    output.githubIssues.length > 0 && !runtime?.githubIssuesCreated
      ? `\n\nGitHub issues queued:\n${output.githubIssues.map((i) => `• ${i}`).join('\n')}`
      : '';

  const parts = [
    `**${output.title}**`,
    '',
    `Routed to your **${intent.label}** worker${providerNote}.`,
    runtime
      ? ''
      : 'Tasks are in your build queue — open Memory to review or sync to GitHub.',
    '',
    output.summary,
    '',
    '**Tasks queued**',
    taskLines,
    '',
    '**Build plan**',
    planLines,
    issueLines,
  ];

  if (runtime) {
    parts.push('', formatWorkforceRuntimeSummary(runtime));
  }

  return parts.filter(Boolean).join('\n');
}

export function buildWorkforceAgentSystemPrompt(templateKey: string, agentName: string, projectName: string): string {
  const label = WORKFORCE_TEMPLATES.find((t) => t.key === templateKey)?.label ?? agentName;
  return `You are the ${label} workforce agent for ${projectName}. The founder runs you from Doxxed Founder Copilot.

Respond ONLY in this markdown structure:
## Title
(one line)
## Summary
(2-3 sentences)
## Tasks
(numbered list, 4-6 concrete items)
## GitHub Issues
(numbered issue titles, optional)
## Build plan
(numbered steps)

Be specific, actionable, and GitHub-ready. No preamble outside the sections.`;
}

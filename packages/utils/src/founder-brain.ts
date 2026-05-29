export type FounderBrainContext = {
  projectName: string;
  lifecycleStage: string;
  launchReadiness: number;
  followerCount: number;
  raiseAllocated: number;
  raiseGoal: number;
  lastBuildHeadlines: string[];
  openTasks: string[];
  currentGoal?: string;
};

export function buildFounderBrainContextBlock(ctx: FounderBrainContext): string {
  return [
    `Project: ${ctx.projectName}`,
    `Stage: ${ctx.lifecycleStage}`,
    `Launch readiness: ${ctx.launchReadiness}%`,
    `Followers: ${ctx.followerCount}`,
    ctx.raiseGoal > 0
      ? `Raise Room: $${ctx.raiseAllocated.toLocaleString()} / $${ctx.raiseGoal.toLocaleString()}`
      : 'Raise Room: not active',
    ctx.currentGoal ? `Current goal: ${ctx.currentGoal}` : '',
    ctx.lastBuildHeadlines.length ? `Recent builds:\n${ctx.lastBuildHeadlines.map((h) => `- ${h}`).join('\n')}` : '',
    ctx.openTasks.length ? `Open tasks:\n${ctx.openTasks.map((t) => `- ${t}`).join('\n')}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

export const FOUNDER_BRAIN_STARTER_QUESTIONS = [
  'What changed this week?',
  'Why does this token exist?',
  'When is launch?',
  'What is the founder building now?',
  'What risks remain?',
];

/** Agent ↔ agent handoffs (CodeGrid-inspired, P1 v1). */

export type AgentBusEventKind =
  | 'RESEARCH_COMPLETED'
  | 'BUILD_COMPLETED'
  | 'BUILD_FAILED'
  | 'CONTENT_DRAFT_READY';

export type AgentBusHandoff = {
  id: string;
  from: 'research' | 'builder' | 'content' | 'founder_brain';
  to: 'builder' | 'content' | 'founder_queue';
  kind: AgentBusEventKind;
  title: string;
  detail: string;
  /** Build queue spec or chat prompt */
  payload: { spec?: string; prompt?: string; sourceTask?: string };
};

export type AgentBusInput = {
  kind: AgentBusEventKind;
  founderId: string;
  projectId?: string | null;
  title: string;
  detail: string;
  sourceTask?: string;
  buildOutput?: { status: string; prUrl?: string | null; result?: string | null };
  researchSummary?: string;
};

/** Declarative v1 rules — returns handoffs to enqueue (no side effects here). */
export function planAgentBusHandoffs(input: AgentBusInput): AgentBusHandoff[] {
  const out: AgentBusHandoff[] = [];
  const id = () => `bus-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  if (input.kind === 'RESEARCH_COMPLETED' && input.researchSummary) {
    const wantsBuild =
      /\b(implement|build|ship|add|fix|competitor|feature|gap)\b/i.test(
        `${input.title} ${input.detail} ${input.researchSummary}`,
      );
    if (wantsBuild) {
      out.push({
        id: id(),
        from: 'research',
        to: 'builder',
        kind: 'RESEARCH_COMPLETED',
        title: 'Implementation proposal from research',
        detail: input.researchSummary.slice(0, 400),
        payload: {
          spec: `Based on research: ${input.title}. ${input.detail}`.slice(0, 1200),
          sourceTask: input.sourceTask,
        },
      });
    }
  }

  if (input.kind === 'BUILD_COMPLETED') {
    const pr = input.buildOutput?.prUrl;
    out.push({
      id: id(),
      from: 'builder',
      to: 'content',
      kind: 'BUILD_COMPLETED',
      title: pr ? 'Draft founder update for shipped work' : 'Draft update for completed build',
      detail: input.buildOutput?.result?.slice(0, 300) ?? input.detail,
      payload: {
        prompt: `Write a short founder build update for: ${input.title}. ${input.detail}`.slice(
          0,
          800,
        ),
        spec: input.sourceTask,
      },
    });
    if (pr) {
      out.push({
        id: id(),
        from: 'builder',
        to: 'founder_queue',
        kind: 'BUILD_COMPLETED',
        title: 'Review and merge PR',
        detail: pr,
        payload: { prompt: `Review PR: ${pr}` },
      });
    }
  }

  if (input.kind === 'BUILD_FAILED') {
    out.push({
      id: id(),
      from: 'builder',
      to: 'founder_queue',
      kind: 'BUILD_FAILED',
      title: 'Builder run needs attention',
      detail: input.buildOutput?.result?.slice(0, 200) ?? input.detail,
      payload: {
        prompt: `Builder failed on: ${input.sourceTask ?? input.title}. Suggest a smaller fix.`,
      },
    });
  }

  return out;
}

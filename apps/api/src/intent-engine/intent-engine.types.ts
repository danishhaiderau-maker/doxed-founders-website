/**
 * Founder Intent Engine — thin Phase 5 skeleton (docs/KERNEL.md §3 #10).
 *
 * Goal → Task steps via AI Gateway, logged to Flight Recorder. This is NOT
 * the full Execution Graph yet — it ships a usable decompose path so Phase 5
 * is not vapor while the heavier graph planner lands.
 */
export type IntentStep = {
  id: string;
  title: string;
  description: string;
  /** Suggested execution target when known. */
  suggestedTarget?: 'terminal' | 'filesystem' | 'browser' | 'cursor' | 'vscode';
  order: number;
};

export type IntentDecomposition = {
  goalId: string;
  goal: string;
  steps: IntentStep[];
  provider?: string;
  model?: string;
  requestId: string;
  createdAt: string;
};

export type DecomposeGoalInput = {
  userId: string;
  goal: string;
  projectId?: string | null;
  maxSteps?: number;
};

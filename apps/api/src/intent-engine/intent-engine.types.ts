/**
 * Founder Intent Engine — Phase 5 skeleton thickening toward Execution Graph.
 *
 * Goal → Task steps via AI Gateway, logged to Flight Recorder.
 * Optional: execute the first *safe* step via Execution Manager
 * (read-only filesystem listing only — never shell/write/git/browser).
 */
export type IntentStep = {
  id: string;
  title: string;
  description: string;
  /** Suggested execution target when known. */
  suggestedTarget?: 'terminal' | 'filesystem' | 'browser' | 'cursor' | 'vscode';
  order: number;
};

export type IntentStepExecution = {
  stepId: string;
  attempted: boolean;
  status: 'success' | 'failed' | 'skipped' | 'unsafe';
  detail: string;
  stdout?: string;
  stderr?: string;
};

export type IntentDecomposition = {
  goalId: string;
  goal: string;
  steps: IntentStep[];
  provider?: string;
  model?: string;
  requestId: string;
  createdAt: string;
  /** Present when executeFirstStep was requested. */
  firstStepExecution?: IntentStepExecution;
};

export type DecomposeGoalInput = {
  userId: string;
  goal: string;
  projectId?: string | null;
  maxSteps?: number;
  /**
   * When true, attempt a safe first-step execution via Execution Manager.
   * Only filesystem readWorkspace / file-read of allowlisted paths run;
   * shell, writes, git, and browser are skipped as unsafe.
   */
  executeFirstStep?: boolean;
  /** Working directory for a safe filesystem first step (defaults to cwd). */
  cwd?: string;
};

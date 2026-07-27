import type { FounderWorkMode } from './founder-agent-mode';

export const FOUNDER_COMPLETION_EVIDENCE_VERSION = 1 as const;

export type FounderCompletionVerdict = 'passed' | 'incomplete';

export interface FounderCompletionEvidenceInput {
  mode: FounderWorkMode;
  goal: string;
  finalAnswer: string;
  requestCompleted: boolean;
  editedFiles: readonly string[];
  passedChecks: readonly string[];
}

export interface FounderCompletionEvidenceReceipt {
  version: typeof FOUNDER_COMPLETION_EVIDENCE_VERSION;
  verdict: FounderCompletionVerdict;
  scope: 'read_only' | 'workspace_change';
  mode: FounderWorkMode;
  editedFileCount: number;
  passedCheckCount: number;
  visualCheckCount: number;
  requirements: readonly string[];
  missing: readonly string[];
}

const IMPLEMENTATION_REQUEST =
  /\b(?:add|build|change|create|delete|deliver|fix|implement|install|integrate|make|modify|remove|rename|replace|ship|update|wire)\b/i;
const UI_PATH =
  /(?:^|\/)(?:apps\/web|browser\/react|components?|pages?|views?|webview|ui)(?:\/|$)|\.(?:css|less|scss|sass|tsx|jsx|html)$/i;
const UNSAFE_COMMAND_COMPOSITION = /(?:\r|\n|&&|\|\||[;|`<>])/;
const VISUAL_SCRIPT =
  /(?:^|[-:._])(?:e2e|playwright|screenshot|ui[-:]?qa|visual)(?:$|[-:._])/i;
const VISUAL_FILE =
  /(?:^|[\\/])(?:installed-[\w.-]+-qa|[\w.-]*(?:playwright|screenshot|visual)[\w.-]*)\.(?:c?js|mjs|ts)$/i;

/**
 * Deterministic completion policy. Provider prose is untrusted; only locally
 * observed edits and successful verification commands can authorize a
 * completion claim.
 */
export function evaluateFounderCompletionEvidence(
  input: FounderCompletionEvidenceInput,
): FounderCompletionEvidenceReceipt {
  const editedFiles = compactList(input.editedFiles);
  const passedChecks = compactList(input.passedChecks);
  const visualChecks = passedChecks.filter(isFounderVisualVerificationCommand);
  const readOnly = input.mode === 'ask' || input.mode === 'plan';
  const implementationRequested = IMPLEMENTATION_REQUEST.test(input.goal);
  const uiChanged = editedFiles.some((file) => UI_PATH.test(normalizePath(file)));
  const requirements: string[] = ['provider_turn_completed', 'nonempty_answer'];
  const missing: string[] = [];

  if (!input.requestCompleted) missing.push('provider turn did not complete');
  if (!input.finalAnswer.trim()) missing.push('final answer is empty');

  if (readOnly) {
    requirements.push('read_only_mode_preserved');
    if (editedFiles.length > 0) {
      missing.push(`${titleCase(input.mode)} mode changed workspace files`);
    }
  } else if (implementationRequested || editedFiles.length > 0) {
    requirements.push('workspace_change_observed', 'verification_command_passed');
    if (editedFiles.length === 0) {
      missing.push('implementation request produced no workspace edit');
    }
    if (editedFiles.length > 0 && passedChecks.length === 0) {
      missing.push('workspace edits have no passing test, build, typecheck, or lint evidence');
    }
  }

  if (uiChanged) {
    requirements.push('visual_check_passed');
    if (visualChecks.length === 0) {
      missing.push('user-facing UI edits have no passing visual or screenshot evidence');
    }
  }

  return {
    version: FOUNDER_COMPLETION_EVIDENCE_VERSION,
    verdict: missing.length === 0 ? 'passed' : 'incomplete',
    scope: editedFiles.length > 0 ? 'workspace_change' : 'read_only',
    mode: input.mode,
    editedFileCount: editedFiles.length,
    passedCheckCount: passedChecks.length,
    visualCheckCount: visualChecks.length,
    requirements,
    missing,
  };
}

/**
 * Accept only dedicated visual runners. A keyword anywhere in a successful
 * shell command is not evidence: `echo screenshot` and chained commands must
 * never satisfy the completion boundary.
 */
export function isFounderVisualVerificationCommand(command: string): boolean {
  const normalized = command.trim().replace(/\s+/g, ' ');
  if (!normalized || normalized.length > 1_000) return false;
  if (UNSAFE_COMMAND_COMPOSITION.test(normalized)) return false;
  if (/^(?:npx|pnpm\s+exec|yarn\s+exec)\s+playwright\s+test(?:\s|$)/i.test(normalized)) {
    return true;
  }
  const packageScript = normalized.match(
    /^(?:npm(?:\.cmd)?|pnpm|yarn)\s+(?:run\s+)?([a-z0-9:._-]+)(?:\s+--(?:\s|$).*)?$/i,
  );
  if (packageScript) return VISUAL_SCRIPT.test(packageScript[1] ?? '');
  const fileRunner = normalized.match(
    /^(?:node(?:\.exe)?|tsx)\s+(?:"([^"]+)"|'([^']+)'|(\S+))(?:\s.*)?$/i,
  );
  return Boolean(fileRunner && VISUAL_FILE.test(
    fileRunner[1] ?? fileRunner[2] ?? fileRunner[3] ?? '',
  ));
}

export function founderWorkModeInstruction(mode: FounderWorkMode): string {
  switch (mode) {
    case 'ask':
      return 'Founder work mode: Ask. Read and explain only. Do not edit files or run commands.';
    case 'plan':
      return 'Founder work mode: Plan. Inspect read-only context and return a concrete plan. Do not edit files or run commands.';
    case 'debug':
      return 'Founder work mode: Debug. Reproduce the defect, make the smallest owned correction, and run a decisive verification command.';
    case 'team':
      return 'Founder work mode: Team. One agent owns edits while advisers remain read-only. Verify every owned change before reporting completion.';
    case 'build':
    default:
      return 'Founder work mode: Build. Implement the requested change as the sole editing owner and run a decisive verification command.';
  }
}

export function renderFounderCompletionReceipt(
  receipt: FounderCompletionEvidenceReceipt,
): string {
  const label = receipt.verdict === 'passed' ? 'Passed' : 'Incomplete';
  const evidence = receipt.scope === 'read_only'
    ? 'read-only response'
    : `${receipt.editedFileCount} file${receipt.editedFileCount === 1 ? '' : 's'} | ${receipt.passedCheckCount} check${receipt.passedCheckCount === 1 ? '' : 's'}`;
  const missing = receipt.missing.length > 0
    ? ` | missing: ${receipt.missing.join('; ')}`
    : '';
  return `\n\n---\n**Founder verification** | ${label} | ${evidence}${missing}`;
}

export function founderToolsForMode(
  mode: FounderWorkMode,
  toolNames: readonly string[],
): string[] {
  if (mode !== 'ask' && mode !== 'plan') return [...toolNames];
  return toolNames.filter((name) => name === 'founder-read-workspace');
}

function compactList(values: readonly string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].slice(0, 100);
}

function normalizePath(value: string): string {
  return value.replaceAll('\\', '/').replace(/^\.?\//, '');
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

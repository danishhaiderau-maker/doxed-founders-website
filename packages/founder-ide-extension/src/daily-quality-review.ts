import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import * as vscode from 'vscode';
import type {
  FounderProjectActivityRecord,
  FounderProjectActivityStore,
} from './project-activity';
import type { FounderAgentAwarenessSummary } from './agent-awareness';
import { resolveCredentials } from './credentials';
import {
  activeCoordinationReason,
  normalizeDailyReviewProbeUrl,
  reviewOwnedFiles,
  selectDailyReviewTaskNames,
  shouldRunDailyQualityReview,
} from './daily-quality-review-state';

const REVIEW_INTERVAL_MS = 24 * 60 * 60 * 1_000;
const SCHEDULER_TICK_MS = 30 * 60 * 1_000;
const TASK_TIMEOUT_MS = 20 * 60 * 1_000;
const STATE_KEY = 'founder.dailyQualityReview.state.v1';

interface DailyQualityReviewState {
  lastAttemptAt?: string;
  lastCompletedAt?: string;
  lastOutcome?: 'pass' | 'attention' | 'deferred';
  lastReportPath?: string;
}

interface DailyQualityReviewDependencies {
  activity: FounderProjectActivityStore;
  workspaceId(): string | null;
  awareness(): FounderAgentAwarenessSummary;
}

interface TaskResult {
  label: string;
  exitCode: number | null;
  outcome: 'passed' | 'failed' | 'timed-out' | 'not-started';
}

interface ProbeResult {
  url: string;
  outcome: 'passed' | 'failed';
  detail: string;
}

export function createDailyQualityReview(
  context: vscode.ExtensionContext,
  dependencies: DailyQualityReviewDependencies,
): vscode.Disposable {
  let running = false;

  const run = async (manual: boolean): Promise<void> => {
    if (running) {
      if (manual) {
        void vscode.window.showInformationMessage('Founder daily quality review is already running.');
      }
      return;
    }

    const config = vscode.workspace.getConfiguration('founderOs.dailySelfQa');
    const enabled = config.get<boolean>('enabled', false);
    const state = context.globalState.get<DailyQualityReviewState>(STATE_KEY, {});
    const lastScheduledBoundary = state.lastCompletedAt
      ?? (state.lastOutcome === 'attention' ? state.lastAttemptAt : undefined);
    if (!manual && !shouldRunDailyQualityReview(enabled, lastScheduledBoundary)) return;

    const folder = vscode.workspace.workspaceFolders?.[0];
    const workspaceId = dependencies.workspaceId();
    if (!folder || !workspaceId) {
      if (manual) {
        void vscode.window.showInformationMessage('Open a project before running daily quality review.');
      }
      return;
    }

    running = true;
    const startedAt = new Date();
    await context.globalState.update(STATE_KEY, {
      ...state,
      lastAttemptAt: startedAt.toISOString(),
    } satisfies DailyQualityReviewState);

    try {
      const coordinationReason = activeCoordinationReason(dependencies.awareness());
      if (coordinationReason) {
        await context.globalState.update(STATE_KEY, {
          ...state,
          lastAttemptAt: startedAt.toISOString(),
          lastOutcome: 'deferred',
        } satisfies DailyQualityReviewState);
        if (manual) void vscode.window.showWarningMessage(coordinationReason);
        return;
      }

      const since = new Date(startedAt.getTime() - REVIEW_INTERVAL_MS);
      const activity = dependencies.activity.recordsFor(workspaceId, since);
      const ownedFiles = reviewOwnedFiles(activity);
      const gitFiles = await gitFilesChangedSince(folder.uri.fsPath);
      const taskResults = ownedFiles.length > 0 || gitFiles.length > 0
        ? await runConfiguredTasks(config.get<string[]>('taskLabels', []))
        : [];
      const probeResults = await probeConfiguredUrls(
        config.get<string[]>('probeUrls', []),
      );
      const report = renderReport({
        workspaceName: folder.name,
        startedAt,
        activity,
        ownedFiles,
        gitFiles,
        taskResults,
        probeResults,
      });
      const reportPath = writeReport(context.globalStorageUri.fsPath, startedAt, report);
      const attention = taskResults.some((result) => result.outcome !== 'passed')
        || probeResults.some((result) => result.outcome !== 'passed')
        || activity.some((record) => record.status === 'failed' || record.status === 'running');
      const completedAt = new Date().toISOString();
      await context.globalState.update(STATE_KEY, {
        lastAttemptAt: startedAt.toISOString(),
        lastCompletedAt: completedAt,
        lastOutcome: attention ? 'attention' : 'pass',
        lastReportPath: reportPath,
      } satisfies DailyQualityReviewState);

      if (manual) {
        const document = await vscode.workspace.openTextDocument(reportPath);
        await vscode.window.showTextDocument(document, { preview: true });
      }
      const message = attention
        ? 'Founder daily review completed with items needing attention.'
        : 'Founder daily review completed with no recorded failures.';
      void vscode.window.showInformationMessage(message, 'Open report').then(async (choice) => {
        if (choice !== 'Open report') return;
        const document = await vscode.workspace.openTextDocument(reportPath);
        await vscode.window.showTextDocument(document, { preview: true });
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      await context.globalState.update(STATE_KEY, {
        ...state,
        lastAttemptAt: startedAt.toISOString(),
        lastOutcome: 'attention',
      } satisfies DailyQualityReviewState);
      void vscode.window.showErrorMessage(`Founder daily quality review failed: ${detail}`);
    } finally {
      running = false;
    }
  };

  const runCommand = vscode.commands.registerCommand(
    'founderOs.runDailyQualityReview',
    () => run(true),
  );
  const configureCommand = vscode.commands.registerCommand(
    'founderOs.configureDailyQualityReview',
    () => vscode.commands.executeCommand(
      'workbench.action.openSettings',
      '@ext:doxxedcrypto.founder-ide-extension daily self QA',
    ),
  );
  const interval = setInterval(() => {
    void run(false);
  }, SCHEDULER_TICK_MS);
  interval.unref?.();
  const startup = setTimeout(() => {
    void run(false);
  }, 30_000);
  startup.unref?.();

  return new vscode.Disposable(() => {
    clearTimeout(startup);
    clearInterval(interval);
    runCommand.dispose();
    configureCommand.dispose();
  });
}

async function gitFilesChangedSince(workspaceRoot: string): Promise<string[]> {
  const [recent, working] = await Promise.all([
    exec('git', ['log', '--since=24 hours ago', '--name-only', '--pretty=format:'], workspaceRoot),
    exec('git', ['status', '--short'], workspaceRoot),
  ]);
  const recentFiles = recent.ok ? recent.stdout.split(/\r?\n/) : [];
  const workingFiles = working.ok
    ? working.stdout.split(/\r?\n/).map((line) => line.slice(3))
    : [];
  return [...new Set(
    [...recentFiles, ...workingFiles]
      .map(normalizeRelativePath)
      .filter(Boolean),
  )].sort();
}

async function runConfiguredTasks(labels: string[]): Promise<TaskResult[]> {
  const tasks = await vscode.tasks.fetchTasks();
  const selectedNames = selectDailyReviewTaskNames(
    tasks.map((task) => ({
      name: task.name,
      group: task.group?.id,
      isDefault: task.group?.isDefault,
    })),
    labels,
  );
  const selected = selectedNames
    .map((name) => tasks.find((task) => task.name === name))
    .filter((task): task is vscode.Task => Boolean(task));
  if (selected.length === 0) return [];
  const results: TaskResult[] = [];
  for (const task of selected) results.push(await executeTask(task));
  return results;
}

async function executeTask(task: vscode.Task): Promise<TaskResult> {
  let execution: vscode.TaskExecution | undefined;
  return new Promise<TaskResult>((resolve) => {
    let settled = false;
    const finish = (result: TaskResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      listener.dispose();
      resolve(result);
    };
    const listener = vscode.tasks.onDidEndTaskProcess((event) => {
      if (!execution || event.execution !== execution) return;
      finish({
        label: task.name,
        exitCode: event.exitCode ?? null,
        outcome: event.exitCode === 0 ? 'passed' : 'failed',
      });
    });
    const timeout = setTimeout(() => {
      execution?.terminate();
      finish({ label: task.name, exitCode: null, outcome: 'timed-out' });
    }, TASK_TIMEOUT_MS);
    void vscode.tasks.executeTask(task).then(
      (started) => {
        execution = started;
      },
      () => finish({ label: task.name, exitCode: null, outcome: 'not-started' }),
    );
  });
}

async function probeConfiguredUrls(configured: string[]): Promise<ProbeResult[]> {
  const credentials = resolveCredentials();
  const apiBase = credentials?.apiBaseUrl.replace(/\/api\/?$/, '').replace(/\/$/, '');
  const values = [...configured];
  if (apiBase) values.unshift(`${apiBase}/api/health`);
  const urls = [...new Set(
    values
      .map(normalizeDailyReviewProbeUrl)
      .filter((value): value is string => Boolean(value)),
  )].slice(0, 10);
  return Promise.all(urls.map(async (url): Promise<ProbeResult> => {
    try {
      const response = await fetch(url, {
        method: 'GET',
        signal: AbortSignal.timeout(10_000),
        redirect: 'follow',
      });
      return {
        url,
        outcome: response.ok ? 'passed' : 'failed',
        detail: `HTTP ${response.status}`,
      };
    } catch (error) {
      return {
        url,
        outcome: 'failed',
        detail: error instanceof Error ? error.message.slice(0, 180) : 'Request failed',
      };
    }
  }));
}

function renderReport(input: {
  workspaceName: string;
  startedAt: Date;
  activity: FounderProjectActivityRecord[];
  ownedFiles: string[];
  gitFiles: string[];
  taskResults: TaskResult[];
  probeResults: ProbeResult[];
}): string {
  const failures = input.activity.filter((record) =>
    record.status === 'failed' || record.status === 'running');
  const checks = [...new Set(input.activity.flatMap((record) => record.checks))].sort();
  const uiChanged = [...input.ownedFiles, ...input.gitFiles].some((file) =>
    /(?:apps\/web|founder-hub|founder-settings|founder-companion|\.css$|\.scss$|\.tsx$)/i.test(file));
  const suggestions = [
    ...(failures.length > 0
      ? ['Review unfinished or failed Founder tasks before starting unrelated repairs.']
      : []),
    ...(input.taskResults.some((result) => result.outcome !== 'passed')
      ? ['Repair only the failing module, rerun its same task, and preserve unrelated changes.']
      : []),
    ...(input.probeResults.some((result) => result.outcome !== 'passed')
      ? ['Confirm the failing service and authentication boundary before changing client code.']
      : []),
    ...(uiChanged
      ? ['Run the installed visual matrix for changed UI states before shipping.']
      : []),
  ];

  return [
    `# ${input.workspaceName} - daily quality review`,
    '',
    `Started ${input.startedAt.toISOString()}. This report covers the preceding 24 hours.`,
    '',
    '## Coordination gate',
    '- No active Founder task lease was present when build, test, and endpoint checks started.',
    '- This review never edits workspace files or performs automatic rollback.',
    '',
    '## Founder-owned activity',
    ...(input.activity.length > 0
      ? input.activity.map((record) =>
        `- ${record.status}: ${record.goal}${record.summary ? ` - ${record.summary}` : ''}`)
      : ['- No Founder task activity was recorded.']),
    '',
    '## Changed files',
    `- Founder-owned: ${listOrNone(input.ownedFiles)}`,
    `- Git activity: ${listOrNone(input.gitFiles)}`,
    '',
    '## Recorded checks',
    `- ${checks.length > 0 ? checks.join('; ') : 'None recorded'}`,
    '',
    '## Workspace tasks',
    ...(input.taskResults.length > 0
      ? input.taskResults.map((result) =>
        `- ${result.outcome}: ${result.label}${result.exitCode == null ? '' : ` (exit ${result.exitCode})`}`)
      : ['- No configured/default build or test tasks were selected.']),
    '',
    '## Service probes',
    ...(input.probeResults.length > 0
      ? input.probeResults.map((result) => `- ${result.outcome}: ${result.url} - ${result.detail}`)
      : ['- No safe health endpoints were configured.']),
    '',
    '## Bounded follow-up',
    ...(suggestions.length > 0
      ? suggestions.map((suggestion) => `- ${suggestion}`)
      : ['- No repair is proposed from the recorded evidence.']),
    '',
    '_A passing review is evidence for these checks only; it is not a public-release declaration._',
    '',
  ].join('\n');
}

function writeReport(storageRoot: string, startedAt: Date, content: string): string {
  const directory = path.join(storageRoot, 'daily-quality-review');
  fs.mkdirSync(directory, { recursive: true });
  const timestamp = startedAt.toISOString().replace(/[:.]/g, '-');
  const reportPath = path.join(directory, `${timestamp}.md`);
  const temp = `${reportPath}.${process.pid}.tmp`;
  fs.writeFileSync(temp, content, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(temp, reportPath);
  return reportPath;
}

function exec(
  executable: string,
  args: string[],
  cwd: string,
): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    execFile(
      executable,
      args,
      { cwd, encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 2_000_000 },
      (error, stdout) => resolve({ ok: !error, stdout: stdout ?? '' }),
    );
  });
}

function normalizeRelativePath(value: string): string {
  return value.trim().replace(/^"+|"+$/g, '').replaceAll('\\', '/');
}

function listOrNone(values: string[]): string {
  return values.length > 0 ? values.slice(0, 80).join(', ') : 'None';
}

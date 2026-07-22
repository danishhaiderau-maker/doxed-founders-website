import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';
import {
  AGENT_PRESENCE_TTL_MS,
  coordinationPrompt,
  findAgentRisks,
  isFreshPresence,
  parsePresence,
  type FounderAgentPresence,
} from './agent-coordination-state';
import { founderPathLeases } from './agent-path-leases';

export interface FounderAgentAwarenessSummary {
  activeCount: number;
  conflictCount: number;
  tasks: Array<{
    id: string;
    title: string;
    branch?: string;
    files: string[];
    conflict: boolean;
  }>;
}

type ActiveLease = {
  file: string;
  presence: FounderAgentPresence;
};

export class FounderAgentAwareness implements vscode.Disposable {
  private readonly instanceId = randomUUID();
  private readonly leases = new Map<string, ActiveLease>();
  private readonly changeEmitter = new vscode.EventEmitter<FounderAgentAwarenessSummary>();
  private readonly timer: NodeJS.Timeout;
  private readonly documentListener: vscode.Disposable;
  private lastWarningKey = '';

  readonly onDidChange = this.changeEmitter.event;

  constructor() {
    this.timer = setInterval(() => this.refreshLeases(), 60_000);
    this.timer.unref?.();
    this.documentListener = vscode.workspace.onDidChangeTextDocument(() => this.refreshLeases());
    this.pruneStaleFiles();
  }

  begin(prompt: string, provider: string): string {
    const taskId = `${this.instanceId}-${randomUUID()}`;
    const presence = this.createPresence(taskId, prompt, provider);
    const file = path.join(coordinationRoot(), `${safeFileName(taskId)}.json`);
    this.leases.set(taskId, { file, presence });
    this.writeLease(file, presence);
    this.emitSummary(taskId);
    return taskId;
  }

  contextFor(taskId: string): string {
    const lease = this.leases.get(taskId);
    if (!lease) return '';
    this.refreshLease(lease);
    const peers = readAllPresences().filter((presence) => presence.id !== taskId);
    const risks = findAgentRisks(lease.presence, peers);
    if (risks.length > 0) {
      const warningKey = risks.map((risk) => risk.peer.id).sort().join('|');
      if (warningKey !== this.lastWarningKey) {
        this.lastWarningKey = warningKey;
        void vscode.window.showWarningMessage(
          `Founder Agents: ${risks[0]!.reason}. The agents received a coordination note before continuing.`,
          'Open Founder',
        ).then((choice) => {
          if (choice === 'Open Founder') {
            void vscode.commands.executeCommand('workbench.view.extension.founderOs');
          }
        });
      }
    }
    this.emitSummary(taskId);
    return coordinationPrompt(lease.presence, peers);
  }

  end(taskId: string): void {
    const lease = this.leases.get(taskId);
    if (!lease) return;
    this.leases.delete(taskId);
    founderPathLeases.releaseTask(taskId);
    try { fs.rmSync(lease.file, { force: true }); } catch { /* best effort */ }
    this.lastWarningKey = '';
    this.emitSummary();
  }

  summary(): FounderAgentAwarenessSummary {
    return this.buildSummary();
  }

  dispose(): void {
    clearInterval(this.timer);
    this.documentListener.dispose();
    for (const taskId of [...this.leases.keys()]) this.end(taskId);
    this.changeEmitter.dispose();
  }

  private createPresence(taskId: string, prompt: string, provider: string): FounderAgentPresence {
    const folder = vscode.workspace.workspaceFolders?.[0];
    const workspacePath = folder?.uri.fsPath ?? '';
    const now = new Date().toISOString();
    return {
      version: 1,
      id: taskId,
      workspacePath,
      workspaceName: folder?.name ?? 'Untitled workspace',
      branch: readGitBranch(workspacePath),
      title: taskTitle(prompt),
      provider,
      status: 'working',
      ownedFiles: openWorkspaceFiles(workspacePath),
      startedAt: now,
      heartbeatAt: now,
    };
  }

  private refreshLeases(): void {
    this.pruneStaleFiles();
    for (const lease of this.leases.values()) this.refreshLease(lease);
    this.emitSummary();
  }

  private refreshLease(lease: ActiveLease): void {
    founderPathLeases.refreshTask(lease.presence.id);
    const claimedFiles = founderPathLeases
      .claimsForTask(lease.presence.id)
      .map((claim) => claim.relativePath);
    lease.presence = {
      ...lease.presence,
      branch: readGitBranch(lease.presence.workspacePath),
      ownedFiles: [...new Set([
        ...claimedFiles,
        ...openWorkspaceFiles(lease.presence.workspacePath),
      ])].slice(0, 80),
      heartbeatAt: new Date().toISOString(),
    };
    this.writeLease(lease.file, lease.presence);
  }

  private writeLease(file: string, presence: FounderAgentPresence): void {
    try {
      fs.mkdirSync(coordinationRoot(), { recursive: true });
      const temp = `${file}.${process.pid}.tmp`;
      fs.writeFileSync(temp, JSON.stringify(presence, null, 2), 'utf8');
      fs.rmSync(file, { force: true });
      fs.renameSync(temp, file);
    } catch {
      // Awareness must never block the coding task.
    }
  }

  private pruneStaleFiles(): void {
    founderPathLeases.prune();
    for (const file of listPresenceFiles()) {
      try {
        const presence = parsePresence(JSON.parse(fs.readFileSync(file, 'utf8')));
        if (!presence || !isFreshPresence(presence)) fs.rmSync(file, { force: true });
      } catch {
        try { fs.rmSync(file, { force: true }); } catch { /* best effort */ }
      }
    }
  }

  private emitSummary(focusTaskId?: string): void {
    this.changeEmitter.fire(this.buildSummary(focusTaskId));
  }

  private buildSummary(focusTaskId?: string): FounderAgentAwarenessSummary {
    const all = readAllPresences();
    const local = focusTaskId
      ? this.leases.get(focusTaskId)?.presence
      : this.leases.values().next().value?.presence as FounderAgentPresence | undefined;
    const workspacePath = local?.workspacePath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
    const tasks = all.filter(
      (presence) => normalizedPath(presence.workspacePath) === normalizedPath(workspacePath),
    );
    const risks = local ? findAgentRisks(local, tasks) : [];
    const conflictingIds = new Set(risks.map((risk) => risk.peer.id));
    return {
      activeCount: tasks.length,
      conflictCount: risks.length,
      tasks: tasks.slice(0, 8).map((presence) => ({
        id: presence.id,
        title: presence.title,
        branch: presence.branch,
        files: presence.ownedFiles.slice(0, 5),
        conflict: presence.id === local?.id ? risks.length > 0 : conflictingIds.has(presence.id),
      })),
    };
  }
}

function coordinationRoot(): string {
  return path.join(os.homedir(), '.founder-ide', 'coordination');
}

function listPresenceFiles(): string[] {
  try {
    return fs.readdirSync(coordinationRoot())
      .filter((name) => name.endsWith('.json'))
      .map((name) => path.join(coordinationRoot(), name));
  } catch {
    return [];
  }
}

function readAllPresences(): FounderAgentPresence[] {
  const now = Date.now();
  const presences: FounderAgentPresence[] = [];
  for (const file of listPresenceFiles()) {
    try {
      const presence = parsePresence(JSON.parse(fs.readFileSync(file, 'utf8')));
      if (presence && isFreshPresence(presence, now)) presences.push(presence);
    } catch {
      // A peer may be atomically replacing its own lease.
    }
  }
  return presences.sort((a, b) => Date.parse(b.heartbeatAt) - Date.parse(a.heartbeatAt));
}

function openWorkspaceFiles(workspacePath: string): string[] {
  if (!workspacePath) return [];
  const root = normalizedPath(workspacePath);
  const files = new Set<string>();
  const documents = [...vscode.workspace.textDocuments].sort((a, b) => Number(b.isDirty) - Number(a.isDirty));
  for (const document of documents) {
    if (document.uri.scheme !== 'file') continue;
    const absolute = normalizedPath(document.uri.fsPath);
    if (absolute !== root && !absolute.startsWith(`${root}/`)) continue;
    files.add(path.relative(workspacePath, document.uri.fsPath).replaceAll('\\', '/'));
    if (files.size >= 80) break;
  }
  return [...files];
}

function readGitBranch(workspacePath: string): string | undefined {
  if (!workspacePath) return undefined;
  try {
    const dotGit = path.join(workspacePath, '.git');
    let gitDir = dotGit;
    if (fs.statSync(dotGit).isFile()) {
      const line = fs.readFileSync(dotGit, 'utf8').trim();
      const target = line.replace(/^gitdir:\s*/i, '');
      gitDir = path.resolve(workspacePath, target);
    }
    const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
    return head.startsWith('ref:') ? head.replace(/^ref:\s*/, '').replace(/^refs\/heads\//, '') : head.slice(0, 8);
  } catch {
    return undefined;
  }
}

function taskTitle(prompt: string): string {
  return prompt.replace(/\s+/g, ' ').trim().slice(0, 140) || 'Founder AI task';
}

function safeFileName(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}

function normalizedPath(value: string): string {
  return value.trim().replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
}

export const __testHooks = {
  coordinationRoot,
  openWorkspaceFiles,
  readGitBranch,
  readAllPresences,
  ttlMs: AGENT_PRESENCE_TTL_MS,
};

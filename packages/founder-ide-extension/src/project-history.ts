import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import {
  parseFounderProjectHistory,
  recordFounderProject,
  renameFounderProject,
  setFounderProjectArchived,
  setFounderProjectPinned,
  visibleFounderProjects,
  type FounderProjectCandidate,
  type FounderProjectRecord,
} from './project-history-state';

const STORAGE_KEY = 'founderOs.projectHistory.v1';
const PIN = new vscode.ThemeIcon('pin');
const PINNED = new vscode.ThemeIcon('pinned');
const RENAME = new vscode.ThemeIcon('edit');
const ARCHIVE = new vscode.ThemeIcon('archive');
const RESTORE = new vscode.ThemeIcon('discard');

interface FounderProjectQuickPickItem extends vscode.QuickPickItem {
  project: FounderProjectRecord;
}

export class FounderProjectHistory implements vscode.Disposable {
  private records: FounderProjectRecord[];
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly context: vscode.ExtensionContext) {
    this.records = parseFounderProjectHistory(
      context.globalState.get<unknown>(STORAGE_KEY),
    );
    this.disposables.push(
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        void this.captureCurrent();
      }),
    );
    void this.captureCurrent();
  }

  async show(): Promise<void> {
    await this.captureCurrent();
    const quickPick = vscode.window.createQuickPick<FounderProjectQuickPickItem>();
    const pickerDisposables: vscode.Disposable[] = [];
    let showArchived = false;
    quickPick.title = 'Founder Projects';
    quickPick.placeholder = 'Search projects by name or location';
    quickPick.matchOnDescription = true;
    quickPick.matchOnDetail = true;
    quickPick.keepScrollPosition = true;
    quickPick.buttons = [
      {
        iconPath: new vscode.ThemeIcon('folder-opened'),
        tooltip: 'Open another folder',
      },
      {
        iconPath: new vscode.ThemeIcon('archive'),
        tooltip: 'Show archived projects',
      },
    ];

    const refresh = (): void => {
      quickPick.items = visibleFounderProjects(this.records, showArchived)
        .map((project) => projectItem(project));
      quickPick.buttons = [
        {
          iconPath: new vscode.ThemeIcon('folder-opened'),
          tooltip: 'Open another folder',
        },
        {
          iconPath: new vscode.ThemeIcon(showArchived ? 'folder-active' : 'archive'),
          tooltip: showArchived ? 'Show active projects' : 'Show archived projects',
        },
      ];
    };

    pickerDisposables.push(
      quickPick.onDidAccept(async () => {
        const selected = quickPick.selectedItems[0]?.project;
        if (!selected) return;
        quickPick.hide();
        await this.open(selected);
      }),
      quickPick.onDidTriggerButton(async (button) => {
        if (button.tooltip === 'Open another folder') {
          const selected = await vscode.window.showOpenDialog({
            canSelectFiles: false,
            canSelectFolders: true,
            canSelectMany: false,
            openLabel: 'Open project',
            title: 'Open a Founder project',
          });
          if (selected?.[0]) {
            quickPick.hide();
            await vscode.commands.executeCommand('vscode.openFolder', selected[0], false);
          }
          return;
        }
        showArchived = !showArchived;
        refresh();
      }),
      quickPick.onDidTriggerItemButton(async ({ item, button }) => {
        const project = item.project;
        const changedAt = new Date().toISOString();
        switch (button.tooltip) {
          case 'Pin project':
            this.records = setFounderProjectPinned(this.records, project.id, true, changedAt);
            break;
          case 'Unpin project':
            this.records = setFounderProjectPinned(this.records, project.id, false, changedAt);
            break;
          case 'Rename project': {
            const name = await vscode.window.showInputBox({
              title: 'Rename Founder project',
              prompt: 'Use a short name that makes this work easy to resume.',
              value: project.name,
              validateInput: (value) =>
                value.trim() ? null : 'Project name is required.',
            });
            if (name) {
              this.records = renameFounderProject(this.records, project.id, name);
            }
            break;
          }
          case 'Archive project':
            this.records = setFounderProjectArchived(this.records, project.id, true, changedAt);
            break;
          case 'Restore project':
            this.records = setFounderProjectArchived(this.records, project.id, false, changedAt);
            break;
        }
        await this.persist();
        refresh();
      }),
      quickPick.onDidHide(() => {
        for (const disposable of pickerDisposables.splice(0)) disposable.dispose();
        quickPick.dispose();
      }),
    );
    refresh();
    quickPick.show();
  }

  dispose(): void {
    for (const disposable of this.disposables.splice(0)) disposable.dispose();
  }

  private async captureCurrent(): Promise<void> {
    const current = currentFounderProject();
    if (!current) return;
    this.records = recordFounderProject(this.records, current);
    await this.persist();
  }

  private async persist(): Promise<void> {
    await this.context.globalState.update(STORAGE_KEY, this.records);
  }

  private async open(project: FounderProjectRecord): Promise<void> {
    if (!projectExists(project)) {
      this.records = setFounderProjectArchived(
        this.records,
        project.id,
        true,
        new Date().toISOString(),
      );
      await this.persist();
      void vscode.window.showWarningMessage(
        `Founder could not find ${project.name}. It was moved to archived projects.`,
      );
      return;
    }
    await vscode.commands.executeCommand(
      'vscode.openFolder',
      vscode.Uri.file(project.uri),
      false,
    );
  }
}

function currentFounderProject(): FounderProjectCandidate | null {
  const workspaceFile = vscode.workspace.workspaceFile;
  if (workspaceFile?.scheme === 'file') {
    return candidate(
      workspaceFile.fsPath,
      path.basename(workspaceFile.fsPath, path.extname(workspaceFile.fsPath)),
      'workspace',
    );
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder || folder.uri.scheme !== 'file') return null;
  return candidate(folder.uri.fsPath, folder.name, 'folder');
}

function candidate(
  uri: string,
  name: string,
  kind: FounderProjectCandidate['kind'],
): FounderProjectCandidate {
  const normalized = path.resolve(uri);
  return {
    id: createHash('sha256')
      .update(`${kind}\n${normalized.toLowerCase()}`)
      .digest('hex')
      .slice(0, 24),
    uri: normalized,
    name: name.trim() || path.basename(normalized),
    kind,
    openedAt: new Date().toISOString(),
  };
}

function projectItem(project: FounderProjectRecord): FounderProjectQuickPickItem {
  const status = [
    project.pinnedAt ? 'Pinned' : '',
    project.archivedAt ? 'Archived' : '',
  ].filter(Boolean).join(' | ');
  return {
    label: project.name,
    description: status || (project.kind === 'workspace' ? 'Workspace' : 'Project'),
    detail: project.uri,
    alwaysShow: true,
    project,
    buttons: [
      {
        iconPath: project.pinnedAt ? PINNED : PIN,
        tooltip: project.pinnedAt ? 'Unpin project' : 'Pin project',
      },
      { iconPath: RENAME, tooltip: 'Rename project' },
      {
        iconPath: project.archivedAt ? RESTORE : ARCHIVE,
        tooltip: project.archivedAt ? 'Restore project' : 'Archive project',
      },
    ],
  };
}

function projectExists(project: FounderProjectRecord): boolean {
  try {
    const stat = fs.statSync(project.uri);
    return project.kind === 'workspace' ? stat.isFile() : stat.isDirectory();
  } catch {
    return false;
  }
}

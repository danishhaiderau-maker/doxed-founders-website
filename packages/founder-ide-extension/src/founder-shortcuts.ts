import * as vscode from 'vscode';
import { resolveCredentials } from './credentials';
import {
  FOUNDER_SHORTCUT_SURFACES,
  FOUNDER_SHORTCUT_VIEW_IDS,
  shortcutEntries,
  type FounderShortcutEntry,
  type FounderShortcutState,
  type FounderShortcutSurface,
} from './founder-shortcuts-state';
import {
  normalizeWorkspaceMode,
  workspaceModeDefinition,
} from './founder-hub-state';

class FounderShortcutItem extends vscode.TreeItem {
  constructor(entry: FounderShortcutEntry) {
    super(entry.label, vscode.TreeItemCollapsibleState.None);
    this.id = entry.id;
    this.description = entry.description;
    this.tooltip = entry.description
      ? `${entry.label} - ${entry.description}`
      : entry.label;
    this.iconPath = new vscode.ThemeIcon(entry.icon);
    if (entry.command) {
      this.command = {
        command: entry.command,
        title: entry.label,
      };
    }
  }
}

class FounderShortcutTreeProvider
  implements vscode.TreeDataProvider<FounderShortcutItem>, vscode.Disposable
{
  private readonly changeEmitter = new vscode.EventEmitter<
    FounderShortcutItem | undefined | null | void
  >();

  readonly onDidChangeTreeData = this.changeEmitter.event;

  constructor(private readonly surface: FounderShortcutSurface) {}

  getTreeItem(element: FounderShortcutItem): vscode.TreeItem {
    return element;
  }

  getChildren(): FounderShortcutItem[] {
    return shortcutEntries(this.surface, currentShortcutState()).map(
      (entry) => new FounderShortcutItem(entry),
    );
  }

  refresh(): void {
    this.changeEmitter.fire();
  }

  dispose(): void {
    this.changeEmitter.dispose();
  }
}

export class FounderShortcutRegistry implements vscode.Disposable {
  private readonly providers: FounderShortcutTreeProvider[] = [];
  private readonly views: vscode.TreeView<FounderShortcutItem>[] = [];

  constructor() {
    for (const surface of FOUNDER_SHORTCUT_SURFACES) {
      const provider = new FounderShortcutTreeProvider(surface);
      const view = vscode.window.createTreeView(FOUNDER_SHORTCUT_VIEW_IDS[surface], {
        treeDataProvider: provider,
        showCollapseAll: false,
      });
      this.providers.push(provider);
      this.views.push(view);
    }
    this.refresh();
  }

  refresh(): void {
    const connected = Boolean(resolveCredentials());
    for (const provider of this.providers) provider.refresh();
    const nodeView = this.views[FOUNDER_SHORTCUT_SURFACES.indexOf('node')];
    nodeView.badge = connected
      ? { value: 1, tooltip: 'Founder Node connected' }
      : undefined;
  }

  dispose(): void {
    for (const provider of this.providers) provider.dispose();
    for (const view of this.views) view.dispose();
    this.providers.length = 0;
    this.views.length = 0;
  }
}

function currentShortcutState(): FounderShortcutState {
  const config = vscode.workspace.getConfiguration('founderOs');
  const mode = normalizeWorkspaceMode(config.get<string>('workspaceMode'));
  return {
    connected: Boolean(resolveCredentials()),
    workspaceName:
      vscode.workspace.name?.trim()
      || vscode.workspace.workspaceFolders?.[0]?.name
      || 'Open a workspace',
    modeLabel: workspaceModeDefinition(mode).label,
  };
}

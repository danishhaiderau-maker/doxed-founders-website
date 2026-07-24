/**
 * Execution-profile selector + persistence.
 *
 * Surfaces the four Founder OS execution profiles as a QuickPick + status-bar
 * menu. The chosen profile maps to a model alias (and the `X-Execution-Profile`
 * header the chat provider already sends). Selection is persisted in workspace
 * state so it survives reloads.
 *
 * Profiles (see task spec / design report §7):
 *   - Turbo      → founder-os-fast      (speed + low managed cost)
 *   - Balanced   → founder-os-auto      (default routing)
 *   - Architect  → founder-os-reasoning (deep reasoning)
 *   - Autonomous → founder-os-reasoning (maps to architect for now)
 *
 * The backend `ExecutionProfileService` (`apps/api/src/routing-engine/`)
 * persists `WorkspaceExecutionProfile`, but no HTTP controller exposes it yet.
 * Until that endpoint exists, we store the choice locally in workspace state.
 * When `/api/routing-engine/profile` lands, `persistToBackend()` can be flipped
 * on with no other changes.
 */
import * as vscode from 'vscode';
import {
  type FounderOsModelAlias,
  type FounderOsModelAliasId,
  FOUNDER_OS_MODELS,
  findModelAlias,
} from './models';

export type ExecutionProfileId = 'turbo' | 'balanced' | 'architect' | 'autonomous';

export interface ExecutionProfile {
  id: ExecutionProfileId;
  label: string;
  detail: string;
  /** Model alias this profile routes through. */
  aliasId: FounderOsModelAliasId;
  /** Icon for the status bar. */
  icon: string;
}

export const EXECUTION_PROFILES: readonly ExecutionProfile[] = [
  {
    id: 'turbo',
    label: 'Turbo',
    detail: 'DeepSeek V4 Flash for fast, cost-efficient work.',
    aliasId: 'founder-os-fast',
    icon: '$(rocket)',
  },
  {
    id: 'balanced',
    label: 'Balanced',
    detail: 'DeepSeek V4 Flash by default, with explicit Pro modes when needed.',
    aliasId: 'founder-os-auto',
    icon: '$(symbol-enum)',
  },
  {
    id: 'architect',
    label: 'Architect',
    detail: 'DeepSeek V4 Pro for deliberate reasoning and architecture.',
    aliasId: 'founder-os-reasoning',
    icon: '$(beaker)',
  },
  {
    id: 'autonomous',
    label: 'Autonomous',
    detail: 'Allow bounded multi-step execution with verification (maps to Reasoning for now).',
    aliasId: 'founder-os-reasoning',
    icon: '$(robot)',
  },
];

const DEFAULT_PROFILE: ExecutionProfileId = 'balanced';
const STATE_KEY = 'founderOs.executionProfile';
const ALIAS_STATE_KEY = 'founderOs.managedModelAlias';

export function findProfile(id: string): ExecutionProfile | undefined {
  return EXECUTION_PROFILES.find((p) => p.id === id);
}

export function profileForAlias(aliasId: FounderOsModelAliasId): ExecutionProfile {
  // Reverse lookup: which profile prefers this alias. founder-os-fast also
  // implies Turbo (cheap+fast), so we treat both code+fast as turbo-ish.
  if (aliasId === 'founder-os-reasoning') {
    return findProfile('architect')!;
  }
  if (aliasId === 'founder-os-auto') {
    return findProfile('balanced')!;
  }
  return findProfile('turbo')!;
}

export class ProfileManager {
  private readonly bar: vscode.StatusBarItem;
  private readonly showStatusBar: boolean;
  private current: ExecutionProfile;
  private currentAliasId: FounderOsModelAliasId;

  constructor(
    private readonly context: vscode.ExtensionContext,
    options: { showStatusBar?: boolean } = {},
  ) {
    this.showStatusBar = options.showStatusBar ?? true;
    this.bar = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      99,
    );
    this.bar.command = 'founderOs.selectProfile';
    context.subscriptions.push(this.bar);

    const stored = context.workspaceState.get<string>(STATE_KEY);
    this.current = findProfile(stored ?? DEFAULT_PROFILE) ?? findProfile(DEFAULT_PROFILE)!;
    const storedAlias = context.workspaceState.get<string>(ALIAS_STATE_KEY);
    this.currentAliasId = findModelAlias(storedAlias ?? '')?.id ?? this.current.aliasId;
  }

  get profile(): ExecutionProfile {
    return this.current;
  }

  /** Model alias the active profile routes through. */
  get alias(): FounderOsModelAlias {
    return findModelAlias(this.currentAliasId) ?? FOUNDER_OS_MODELS[0];
  }

  /** Show the status-bar item reflecting the active profile. */
  show(): void {
    this.bar.text = `${this.current.icon} ${this.current.label}`;
    this.bar.tooltip = `Founder OS execution profile: ${this.current.label}.\nModel: ${this.alias.id}.\nClick to change.`;
    if (this.showStatusBar) this.bar.show();
    else this.bar.hide();
  }

  /** Open the QuickPick and apply the selection. */
  async selectProfile(): Promise<void> {
    const items: (vscode.QuickPickItem & { profile: ExecutionProfile })[] =
      EXECUTION_PROFILES.map((p) => ({
        label: `${p.icon} ${p.label}`,
        detail: p.detail,
        description: p.aliasId,
        picked: p.id === this.current.id,
        profile: p,
      }));
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: 'Select an execution profile for Founder OS chat',
      title: 'Founder OS — Execution Profile',
    });
    if (!picked) return;
    await this.setProfile(picked.profile.id);
  }

  /** Programmatically set + persist the profile. */
  async setProfile(id: ExecutionProfileId): Promise<void> {
    const next = findProfile(id);
    if (!next) return;
    if (next.id === this.current.id) {
      this.currentAliasId = next.aliasId;
      await this.context.workspaceState.update(ALIAS_STATE_KEY, next.aliasId);
      this.show();
      return;
    }
    this.current = next;
    this.currentAliasId = next.aliasId;
    await this.context.workspaceState.update(STATE_KEY, next.id);
    await this.context.workspaceState.update(ALIAS_STATE_KEY, next.aliasId);
    this.show();
    // Best-effort backend persistence. No controller exists for this yet, so
    // we don't actually fire the request — flip `persistToBackend` when
    // `/api/routing-engine/profile` ships.
    void persistToBackend(next).catch(() => undefined);
    void vscode.window.showInformationMessage(
      `Founder OS profile set to ${next.label} (model: ${next.aliasId}).`,
    );
  }

  /** Select a concrete Founder managed route immediately. */
  async setAlias(id: FounderOsModelAliasId): Promise<void> {
    const alias = findModelAlias(id);
    if (!alias) throw new Error('Unknown Founder managed model route.');
    this.currentAliasId = alias.id;
    this.current = profileForAlias(alias.id);
    await Promise.all([
      this.context.workspaceState.update(ALIAS_STATE_KEY, alias.id),
      this.context.workspaceState.update(STATE_KEY, this.current.id),
    ]);
    this.show();
    void vscode.window.showInformationMessage(`${alias.name} is now active.`);
  }

  dispose(): void {
    this.bar.dispose();
  }
}

/**
 * Stub for backend persistence. The `ExecutionProfileService` exists server-side
 * but has no HTTP controller yet — when one is added at `/api/routing-engine/profile`,
 * replace the body with a fetch that PUTs `{ workspaceId, profile }` using the
 * founder-node bearer. Kept as a no-op so the call site is already wired.
 */
async function persistToBackend(_profile: ExecutionProfile): Promise<void> {
  return;
}

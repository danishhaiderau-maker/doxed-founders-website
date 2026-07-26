import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { readVaultConfig, resolveCredentials } from './credentials';
import {
  defaultFounderEntitlements,
  fetchFounderIdeEntitlements,
  type FounderEntitlementState,
} from './entitlements';
import {
  type ExecutionProfile,
  type ExecutionProfileId,
} from './profile-manager';
import {
  FOUNDER_WORKSPACE_MODES,
  normalizeWorkspaceMode,
  workspaceModeDefinition,
  type FounderWorkspaceMode,
} from './founder-hub-state';
import {
  FOUNDER_OS_MODELS,
  type FounderOsModelAlias,
  type FounderOsModelAliasId,
} from './models';
import {
  parsePersonalAiHeaders,
  type PersonalAiProfileDraft,
  type PersonalAiProfileStore,
} from './personal-ai-profiles';

type FounderSettingsAction =
  | 'openChat'
  | 'openConnections'
  | 'selectModel'
  | 'openAdvancedSettings'
  | 'openNodeConfig'
  | 'manageConnection'
  | 'upgradePlan'
  | 'runDailyQualityReview'
  | 'configureDailyQualityReview'
  | 'toggleDailyQualityReview'
  | 'signIn'
  | 'signOut';

type FounderSettingsTab = 'account' | 'ai' | 'infrastructure' | 'connections' | 'advanced';

interface FounderSettingsMessage {
  type:
    | 'action'
    | 'selectTab'
    | 'selectMode'
    | 'selectProfile'
    | 'selectManagedAlias'
    | 'savePersonalProfile'
    | 'selectPersonalProfile'
    | 'testPersonalProfile'
    | 'togglePersonalProfile'
    | 'deletePersonalProfile';
  action?: FounderSettingsAction;
  tab?: FounderSettingsTab;
  mode?: FounderWorkspaceMode;
  profile?: ExecutionProfileId;
  alias?: FounderOsModelAliasId;
  profileId?: string;
  enabled?: boolean;
  draft?: {
    id?: string;
    name?: string;
    kind?: string;
    baseUrl?: string;
    apiKey?: string;
    model?: string;
    visionModel?: string;
    useForVisuals?: boolean;
    headers?: string;
  };
}

export interface FounderSettingsDependencies {
  getProfile(): ExecutionProfile;
  setProfile(id: ExecutionProfileId): Promise<void>;
  getManagedAlias(): FounderOsModelAlias;
  setManagedAlias(id: FounderOsModelAliasId): Promise<void>;
  personalAiProfiles: PersonalAiProfileStore;
}

export class FounderSettingsPanel implements vscode.Disposable {
  static readonly viewType = 'founderOs.settings';

  private panel: vscode.WebviewPanel | undefined;
  private panelDisposables: vscode.Disposable[] = [];
  private entitlementState: FounderEntitlementState =
    defaultFounderEntitlements('signed-out');
  private entitlementGeneration = 0;
  private activeTab: FounderSettingsTab = 'account';

  constructor(private readonly dependencies: FounderSettingsDependencies) {}

  show(tab: FounderSettingsTab = 'account'): void {
    this.activeTab = normalizeFounderSettingsTab(tab);
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Active);
      this.refresh();
      void this.refreshEntitlements();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      FounderSettingsPanel.viewType,
      'Founder Settings',
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [],
      },
    );
    this.panel = panel;
    this.panelDisposables.push(
      panel.onDidDispose(() => this.disposePanel()),
      panel.webview.onDidReceiveMessage((message: FounderSettingsMessage) =>
        this.handleMessage(message),
      ),
    );
    this.refresh();
    void this.refreshEntitlements();
  }

  refresh(): void {
    if (!this.panel) return;
    this.panel.webview.html = this.renderHtml();
  }

  dispose(): void {
    this.panel?.dispose();
    this.disposePanel();
  }

  private disposePanel(): void {
    this.entitlementGeneration += 1;
    this.panel = undefined;
    for (const disposable of this.panelDisposables) disposable.dispose();
    this.panelDisposables = [];
  }

  private async refreshEntitlements(): Promise<void> {
    const generation = ++this.entitlementGeneration;
    const next = await fetchFounderIdeEntitlements(resolveCredentials());
    if (!this.panel || generation !== this.entitlementGeneration) return;
    this.entitlementState = next;
    this.refresh();
  }

  private async handleMessage(message: FounderSettingsMessage): Promise<void> {
    if (message.type === 'selectTab' && isFounderSettingsTab(message.tab)) {
      this.activeTab = message.tab;
      return;
    }

    if (message.type === 'selectMode' && message.mode) {
      const mode = normalizeWorkspaceMode(message.mode);
      await vscode.workspace
        .getConfiguration('founderOs')
        .update('workspaceMode', mode, vscode.ConfigurationTarget.Global);
      this.refresh();
      return;
    }

    if (message.type === 'selectProfile' && message.profile) {
      await this.dependencies.personalAiProfiles.select(null);
      await this.dependencies.setProfile(message.profile);
      await this.dependencies.setManagedAlias(this.dependencies.getManagedAlias().id);
      this.refresh();
      return;
    }

    if (message.type === 'selectManagedAlias' && message.alias) {
      await this.dependencies.personalAiProfiles.select(null);
      await this.dependencies.setManagedAlias(message.alias);
      this.refresh();
      return;
    }

    if (message.type === 'savePersonalProfile' && message.draft) {
      try {
        const draft = personalDraftFromMessage(message.draft);
        const saved = await this.dependencies.personalAiProfiles.save(draft);
        if (!saved.useForVisuals) {
          await this.dependencies.personalAiProfiles.select(saved.id);
        }
        void vscode.window.showInformationMessage(
          saved.useForVisuals
            ? `${saved.name} will read screenshots. Your chat model is unchanged.`
            : `${saved.name} saved securely and selected.`,
        );
      } catch (error) {
        void vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error));
      }
      this.refresh();
      return;
    }

    if (message.type === 'selectPersonalProfile' && message.profileId) {
      try {
        await this.dependencies.personalAiProfiles.select(message.profileId);
        const selected = this.dependencies.personalAiProfiles.get(message.profileId);
        void vscode.window.showInformationMessage(`${selected?.name ?? 'Personal AI'} is now active.`);
      } catch (error) {
        void vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error));
      }
      this.refresh();
      return;
    }

    if (message.type === 'testPersonalProfile' && message.profileId) {
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Testing Personal AI', cancellable: false },
        async () => {
          const result = await this.dependencies.personalAiProfiles.probe(message.profileId!);
          if (result.ok) void vscode.window.showInformationMessage(result.message);
          else void vscode.window.showErrorMessage(result.message);
        },
      );
      return;
    }

    if (message.type === 'togglePersonalProfile' && message.profileId) {
      try {
        await this.dependencies.personalAiProfiles.setEnabled(message.profileId, Boolean(message.enabled));
      } catch (error) {
        void vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error));
      }
      this.refresh();
      return;
    }

    if (message.type === 'deletePersonalProfile' && message.profileId) {
      const profile = this.dependencies.personalAiProfiles.get(message.profileId);
      if (!profile) return;
      const answer = await vscode.window.showWarningMessage(
        `Delete ${profile.name}? Its saved key and headers will be removed from encrypted storage.`,
        { modal: true },
        'Delete',
      );
      if (answer === 'Delete') await this.dependencies.personalAiProfiles.delete(profile.id);
      this.refresh();
      return;
    }

    if (message.type !== 'action' || !message.action) return;
    switch (message.action) {
      case 'openChat':
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      case 'openConnections':
        await vscode.commands.executeCommand('founderOs.openConnections');
        break;
      case 'selectModel':
        await vscode.commands.executeCommand('founderOs.selectModel');
        break;
      case 'openAdvancedSettings':
        await vscode.commands.executeCommand(
          'workbench.action.openSettings',
          '@ext:doxxedcrypto.founder-ide-extension',
        );
        break;
      case 'openNodeConfig':
        await vscode.commands.executeCommand('founderOs.openVaultConfig');
        break;
      case 'manageConnection':
        await vscode.commands.executeCommand('founderOs.manage');
        break;
      case 'upgradePlan':
        await vscode.env.openExternal(
          vscode.Uri.parse('https://doxxedcrypto.digital/settings/builder'),
        );
        break;
      case 'runDailyQualityReview':
        await vscode.commands.executeCommand('founderOs.runDailyQualityReview');
        break;
      case 'configureDailyQualityReview':
        await vscode.commands.executeCommand('founderOs.configureDailyQualityReview');
        break;
      case 'toggleDailyQualityReview': {
        const quality = vscode.workspace.getConfiguration('founderOs.dailySelfQa');
        const enabled = quality.get<boolean>('enabled', false);
        await quality.update('enabled', !enabled, vscode.ConfigurationTarget.Global);
        void vscode.window.showInformationMessage(
          `Founder daily quality review ${enabled ? 'disabled' : 'enabled'}.`,
        );
        break;
      }
      case 'signIn':
        await vscode.commands.executeCommand('founderOs.signIn');
        break;
      case 'signOut':
        await vscode.commands.executeCommand('founderOs.signOut');
        break;
    }
    this.refresh();
    if (message.action === 'signIn' || message.action === 'signOut') {
      void this.refreshEntitlements();
    }
  }

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const config = vscode.workspace.getConfiguration('founderOs');
    const dailyQualityEnabled = vscode.workspace
      .getConfiguration('founderOs.dailySelfQa')
      .get<boolean>('enabled', false);
    const mode = normalizeWorkspaceMode(config.get<string>('workspaceMode'));
    const modeDefinition = workspaceModeDefinition(mode);
    const profile = this.dependencies.getProfile();
    const managedAlias = this.dependencies.getManagedAlias();
    const personalProfiles = this.dependencies.personalAiProfiles.list();
    const activePersonalId = this.dependencies.personalAiProfiles.activeId();
    const activePersonal = personalProfiles.find((candidate) => candidate.id === activePersonalId);
    const credentials = resolveCredentials();
    const vault = readVaultConfig();
    const connected = Boolean(credentials);
    const accountLabel = connected
      ? vault?.founderId?.trim() || 'Doxxed account'
      : 'Not signed in';
    const nodeLabel = connected
      ? vault?.label?.trim() || 'This computer'
      : 'Not connected';
    const entitlement = this.entitlementState.value;
    const managedTokens = entitlement.managedTokens;
    const planLabel = entitlement.plan === 'builder'
      ? 'Founder Builder'
      : entitlement.plan === 'team'
        ? entitlement.team?.name || 'Founder Team'
        : 'Founder Free';
    const planSummary = entitlement.plan === 'free'
      ? 'A weekly managed allowance for questions, planning, and small edits.'
      : entitlement.plan === 'builder'
        ? 'Managed DeepSeek, coordination, and remote control for active builders.'
        : 'A shared allowance with team roles, coordination, and audit.';
    const planPrice = entitlement.priceCentsMonthly == null
      ? entitlement.plan === 'team' ? 'Contact plan' : ''
      : entitlement.priceCentsMonthly === 0
        ? 'Free'
        : `$${(entitlement.priceCentsMonthly / 100).toFixed(0)}/month`;
    const usagePercent = managedTokens.cap > 0
      ? Math.min(100, Math.max(0, (managedTokens.used / managedTokens.cap) * 100))
      : 0;
    const remainingPercent = managedTokens.cap > 0
      ? Math.min(100, Math.max(0, (managedTokens.remaining / managedTokens.cap) * 100))
      : 0;
    const reservedPercent = managedTokens.cap > 0
      ? Math.min(100, Math.max(0, (managedTokens.reserved / managedTokens.cap) * 100))
      : 0;
    const entitlementStatus = this.entitlementState.source === 'live'
      ? managedTokens.eligible
        ? 'Active'
        : 'Needs attention'
      : this.entitlementState.source === 'signed-out'
        ? 'Sign in required'
        : 'Live usage unavailable';
    const expiryLabel = managedTokens.daysRemaining == null
      ? 'Allowance status is checked through Founder Node'
      : `${managedTokens.daysRemaining} day${managedTokens.daysRemaining === 1 ? '' : 's'} remaining`;
    const renewalLabel = managedTokens.resetsOrExpiresAt
      ? `Renews ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(managedTokens.resetsOrExpiresAt))}`
      : expiryLabel;
    const lowQuota = this.entitlementState.source === 'live'
      && managedTokens.eligible
      && remainingPercent <= 15;
    const reservationLabel = managedTokens.reserved > 0
      ? `${reservedPercent < 0.1 ? '<0.1' : reservedPercent.toFixed(1)}% reserved by work in progress`
      : 'No quota currently reserved';
    const formatWeightedTokens = (value: number): string => new Intl.NumberFormat(
      undefined,
      { notation: 'compact', maximumFractionDigits: 1 },
    ).format(Math.max(0, value));
    const quotaLabel = managedTokens.cap > 0
      ? `${formatWeightedTokens(managedTokens.cap)} weighted tokens/week`
      : 'Managed quota';
    const remainingLabel = managedTokens.cap > 0
      ? `${formatWeightedTokens(managedTokens.remaining)} remaining (${remainingPercent.toFixed(0)}%)`
      : `${remainingPercent.toFixed(0)}% available`;

    const modeButtons = FOUNDER_WORKSPACE_MODES.map(
      (candidate) => `
        <button class="segment ${candidate.id === mode ? 'selected' : ''}" type="button"
          data-mode="${candidate.id}" aria-pressed="${candidate.id === mode}">
          ${escapeHtml(candidate.label)}
        </button>`,
    ).join('');

    const modelButtons = FOUNDER_OS_MODELS.map(
      (candidate) => `
        <button class="choice ${!activePersonal && candidate.id === managedAlias.id ? 'selected' : ''}" type="button"
          data-alias="${candidate.id}" aria-pressed="${!activePersonal && candidate.id === managedAlias.id}">
          <span class="choice-title">${escapeHtml(candidate.name.replace('Founder OS ', ''))}</span>
          <span>${escapeHtml(candidate.detail)}</span>
        </button>`,
    ).join('');

    const connectionRows = [
      ['GitHub', 'Source, pull requests and reviews'],
      ['Vercel', 'Web previews and production deploys'],
      ['Railway', 'Services, workers and live logs'],
      ['Neon', 'Postgres databases and migrations'],
    ]
      .map(
        ([name, detail]) => `
          <div class="connection-row">
            <div><strong>${name}</strong><span>${detail}</span></div>
            <button class="link-button" type="button" data-action="openConnections">Manage</button>
          </div>`,
      )
      .join('');

    const personalProfileRows = personalProfiles.length > 0
      ? personalProfiles.map((candidate) => `
          <div class="profile-row ${candidate.id === activePersonalId ? 'active' : ''}">
            <div class="profile-copy">
              <div><strong>${escapeHtml(candidate.name)}</strong><span class="route-badge">${candidate.kind === 'ollama' ? 'Local' : 'Personal'}</span>${candidate.id === activePersonalId ? '<span class="active-badge">Active</span>' : ''}${candidate.useForVisuals ? '<span class="active-badge">Screenshots</span>' : ''}</div>
              <span>${escapeHtml(candidate.model)} &middot; ${escapeHtml(candidate.baseUrl)}${candidate.useForVisuals ? ` &middot; vision ${escapeHtml(candidate.visionModel)}` : ''}</span>
              <span>${candidate.hasApiKey ? 'Encrypted key saved' : 'No key required'}${candidate.headerNames.length ? ` &middot; ${candidate.headerNames.length} custom header${candidate.headerNames.length === 1 ? '' : 's'}` : ''}</span>
            </div>
            <div class="profile-actions">
              ${candidate.enabled && candidate.id !== activePersonalId ? `<button class="link-button" type="button" data-personal-select="${escapeHtml(candidate.id)}">Use</button>` : ''}
              <button class="link-button" type="button" data-personal-test="${escapeHtml(candidate.id)}">Test</button>
              <button class="link-button" type="button" data-personal-edit="${escapeHtml(candidate.id)}">Edit</button>
              <button class="link-button" type="button" data-personal-toggle="${escapeHtml(candidate.id)}" data-enabled="${!candidate.enabled}">${candidate.enabled ? 'Disable' : 'Enable'}</button>
              <button class="link-button danger" type="button" data-personal-delete="${escapeHtml(candidate.id)}">Delete</button>
            </div>
          </div>`).join('')
      : '<div class="empty-state"><strong>No personal AI yet</strong><span>Add an OpenAI-compatible provider or local Ollama model below.</span></div>';
    const personalProfilesJson = JSON.stringify(personalProfiles).replaceAll('<', '\\u003c');
    const activeTabJson = JSON.stringify(this.activeTab);

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style nonce="${nonce}">
    :root {
      color-scheme: light dark;
      --accent: #2f80ed;
      --positive: #35b779;
      --warning: #e4a853;
      --border: color-mix(in srgb, var(--vscode-foreground) 16%, transparent);
      --muted: color-mix(in srgb, var(--vscode-foreground) 62%, transparent);
      --surface: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 8%);
      --surface-hover: color-mix(in srgb, var(--vscode-editor-background) 86%, var(--vscode-foreground) 14%);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      letter-spacing: 0;
    }
    button { color: inherit; font: inherit; letter-spacing: 0; }
    button:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
    .page { width: min(860px, calc(100% - 40px)); margin: 0 auto; padding: 30px 0 64px; }
    .header { display: grid; gap: 18px; padding-bottom: 24px; }
    .identity { display: flex; align-items: center; gap: 13px; min-width: 0; }
    .mark {
      display: grid; width: 38px; height: 38px; place-items: center;
      border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
      color: var(--positive); font-size: 20px; font-weight: 650;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: 22px; font-weight: 650; }
    .subtitle, .section-copy, .choice span, .connection-row span, .note { color: var(--muted); }
    .subtitle { padding-top: 4px; font-size: 12px; }
    .tabs {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 3px; padding: 3px; border: 1px solid var(--border);
      width: 100%; border-radius: 7px; background: var(--surface);
    }
    .tab, .segment {
      min-height: 30px; border: 0; border-radius: 5px; background: transparent;
      min-width: 0; color: var(--muted); cursor: pointer; padding: 0 8px;
    }
    .tab { min-height: 36px; line-height: 1.25; white-space: normal; }
    .segment { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tab.selected, .segment.selected { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .panel { display: none; }
    .panel.selected { display: block; animation: panel-in 160ms ease-out; }
    .section { padding: 28px 0; border-top: 1px solid var(--border); }
    .section:first-child { border-top: 0; }
    h2 { font-size: 15px; font-weight: 650; }
    .section-copy { padding-top: 6px; max-width: 620px; line-height: 1.55; }
    .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: 18px; }
    .status-item, .connection-row, .profile-row {
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      min-width: 0; padding: 14px 16px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface);
    }
    .status-copy, .connection-row > div { display: grid; min-width: 0; gap: 4px; }
    .status-copy strong, .connection-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .status-copy span, .connection-row span { font-size: 11px; line-height: 1.4; }
    .dot { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 50%; background: var(--warning); }
    .dot.online { background: var(--positive); }
    .status-pill {
      flex: 0 0 auto; min-width: 54px; padding: 4px 8px; border: 1px solid var(--border);
      border-radius: 999px; color: var(--muted); font-size: 10px; line-height: 1.2;
      text-align: center; background: var(--vscode-editor-background);
    }
    .status-pill.healthy { border-color: color-mix(in srgb, var(--positive) 52%, var(--border)); color: var(--positive); }
    .status-lead { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .segments { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px; max-width: 520px; margin-top: 18px; padding: 3px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); }
    .mode-detail { display: grid; gap: 4px; padding-top: 14px; }
    .mode-detail span { color: var(--muted); }
    .choices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: 18px; }
    .choice { display: grid; min-height: 82px; align-content: center; gap: 6px; padding: 14px 16px; text-align: left; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); cursor: pointer; }
    .choice:hover, .connection-row:hover, .profile-row:hover { background: var(--surface-hover); }
    .choice.selected { border-color: var(--vscode-focusBorder); box-shadow: inset 3px 0 0 var(--accent); }
    .choice-title { color: var(--vscode-editor-foreground) !important; font-weight: 650; }
    .connections { display: grid; gap: 8px; padding-top: 18px; }
    .button-row { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 18px; }
    .primary, .secondary, .link-button {
      min-height: 32px; border-radius: 6px; cursor: pointer; padding: 0 13px;
    }
    .primary { border: 1px solid var(--vscode-button-border, transparent); background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .primary:hover { background: var(--vscode-button-hoverBackground); }
    .secondary { border: 1px solid var(--border); background: var(--surface); }
    .secondary:hover { background: var(--surface-hover); }
    .link-button { border: 0; background: transparent; color: var(--vscode-textLink-foreground); }
    .link-button.danger { color: var(--vscode-errorForeground); }
    .note { padding-top: 14px; font-size: 11px; line-height: 1.55; }
    .managed-row {
      display: flex; align-items: center; justify-content: space-between; gap: 20px;
      margin-top: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 7px;
      background: var(--surface);
    }
    .managed-row > div { display: grid; gap: 4px; }
    .managed-row span { color: var(--muted); font-size: 11px; line-height: 1.45; }
    .usage-head, .usage-values {
      display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
    }
    .usage-card {
      display: grid; gap: 12px; margin-top: 18px; padding: 16px;
      border: 1px solid var(--border); border-radius: 7px; background: var(--surface);
    }
    .usage-head strong { font-size: 15px; }
    .usage-head span, .usage-values, .usage-message { color: var(--muted); font-size: 11px; }
    .usage-values strong { color: var(--vscode-editor-foreground); font-size: 13px; }
    .progress { width: 100%; height: 6px; overflow: hidden; border: 0; border-radius: 3px; background: var(--surface-hover); }
    .progress::-webkit-progress-bar { background: var(--surface-hover); }
    .progress::-webkit-progress-value { background: var(--positive); transition: width 180ms ease-out; }
    .usage-message { line-height: 1.5; }
    .usage-warning { color: var(--warning); font-size: 11px; font-weight: 600; line-height: 1.45; }
    .profile-row.active { border-color: color-mix(in srgb, var(--positive) 65%, var(--border)); }
    .profile-copy { display: grid; min-width: 0; gap: 4px; }
    .profile-copy > div { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
    .profile-copy > span { color: var(--muted); font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
    .route-badge, .active-badge { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 650; }
    .route-badge { background: var(--surface-hover); color: var(--muted); }
    .active-badge { background: color-mix(in srgb, var(--positive) 18%, transparent); color: var(--positive); }
    .profile-actions { display: flex; flex: 0 0 auto; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    .empty-state { display: grid; gap: 5px; margin-top: 18px; padding: 18px; border: 1px dashed var(--border); border-radius: 7px; }
    .empty-state span { color: var(--muted); font-size: 11px; }
    .profile-form { display: grid; gap: 13px; margin-top: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .field { display: grid; min-width: 0; gap: 6px; }
    .field.wide { grid-column: 1 / -1; }
    .field label { color: var(--muted); font-size: 11px; font-weight: 600; }
    .field input, .field select, .field textarea {
      width: 100%; min-height: 34px; border: 1px solid var(--vscode-input-border, var(--border)); border-radius: 5px;
      padding: 7px 9px; background: var(--vscode-input-background); color: var(--vscode-input-foreground); font: inherit;
    }
    .field textarea { min-height: 68px; resize: vertical; font-family: var(--vscode-editor-font-family); }
    .field input:focus, .field select:focus, .field textarea:focus { outline: 1px solid var(--vscode-focusBorder); outline-offset: -1px; }
    .form-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .form-title span, .field-hint { color: var(--muted); font-size: 11px; line-height: 1.4; }
    .check-field { display: flex; align-items: flex-start; gap: 9px; color: var(--text); font-size: 12px; line-height: 1.45; }
    .check-field input { width: 16px; height: 16px; margin: 1px 0 0; accent-color: var(--accent); }
    @keyframes panel-in {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) {
      .panel.selected { animation: none; }
    }
    @media (max-width: 680px) {
      .page { width: min(100% - 28px, 860px); padding-top: 24px; }
      .status-grid, .choices, .form-grid { grid-template-columns: 1fr; }
      .field.wide { grid-column: auto; }
      .profile-row { align-items: flex-start; flex-direction: column; }
      .profile-actions { justify-content: flex-start; }
      .tabs { grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); }
    }
  </style>
</head>
<body>
  <main class="page">
    <header class="header">
      <div class="identity">
        <div class="mark" aria-hidden="true">F</div>
        <div><h1>Founder Settings</h1><p class="subtitle">One workspace. Local when you want it, connected when you need it.</p></div>
      </div>
      <nav class="tabs" aria-label="Founder Settings sections">
        <button class="tab selected" type="button" data-tab="account">Account</button>
        <button class="tab" type="button" data-tab="ai">AI</button>
        <button class="tab" type="button" data-tab="infrastructure">Local &amp; Cloud</button>
        <button class="tab" type="button" data-tab="connections">Connections</button>
        <button class="tab" type="button" data-tab="advanced">Advanced</button>
      </nav>
    </header>

    <div class="panel selected" data-panel="account">
      <section class="section">
        <h2>Identity and Node</h2>
        <p class="section-copy">Your Doxxed account and the background Founder Node share one secure connection.</p>
        <div class="status-grid">
          <div class="status-item">
            <div class="status-lead"><span class="dot ${connected ? 'online' : ''}"></span><div class="status-copy"><strong>${escapeHtml(accountLabel)}</strong><span>${connected ? 'Account connected' : 'Sign in to connect this workspace'}</span></div></div>
            <button class="link-button" type="button" data-action="${connected ? 'signOut' : 'signIn'}">${connected ? 'Sign out' : 'Sign in'}</button>
          </div>
          <div class="status-item">
            <div class="status-lead"><span class="dot ${connected ? 'online' : ''}"></span><div class="status-copy"><strong>Founder Node</strong><span>${escapeHtml(nodeLabel)}</span></div></div>
            <button class="link-button" type="button" data-action="manageConnection">Manage</button>
          </div>
        </div>
      </section>
      <section class="section">
        <h2>Workspace</h2>
        <p class="section-copy">Open Founder Chat or continue configuring the services used by this workspace.</p>
        <div class="button-row"><button class="primary" type="button" data-action="openChat">Open Founder Chat</button><button class="secondary" type="button" data-action="openConnections">Manage connections</button></div>
      </section>
      <section class="section">
        <h2>Plan and usage</h2>
        <p class="section-copy">${escapeHtml(planSummary)} Personal API keys and local models remain separate.</p>
        <div class="usage-card">
          <div class="usage-head"><strong>${escapeHtml(planLabel)}</strong><span>${escapeHtml([planPrice, entitlementStatus].filter(Boolean).join(' | '))}</span></div>
          <div class="usage-values"><strong>${usagePercent.toFixed(0)}% used</strong><span>${escapeHtml(quotaLabel)}</span></div>
          <progress class="progress" aria-label="${escapeHtml(planLabel)} quota usage" max="100" value="${usagePercent.toFixed(2)}">${usagePercent.toFixed(0)}%</progress>
          <div class="usage-values"><span>${escapeHtml(remainingLabel)}</span><span>${escapeHtml(reservationLabel)}</span></div>
          <div class="usage-values"><span>${escapeHtml(renewalLabel)}</span><span>Personal keys and local AI stay outside this quota</span></div>
          <div class="usage-values"><span>${entitlement.features.coordination ? 'Agent coordination' : 'Focused agent'}</span><span>${entitlement.features.remoteControl ? 'Remote control included' : 'Remote control on Builder'}</span></div>
          ${lowQuota ? '<p class="usage-warning">Managed quota is running low. Switch to personal or local AI, or upgrade before starting a large task.</p>' : ''}
          ${entitlement.message ? `<p class="usage-message">${escapeHtml(entitlement.message)}</p>` : ''}
          ${entitlement.plan === 'free' ? '<div class="button-row"><button class="primary" type="button" data-action="upgradePlan">View Founder Builder</button></div>' : ''}
        </div>
      </section>
    </div>

    <div class="panel" data-panel="ai">
      <section class="section">
        <h2>Founder AI</h2>
        <p class="section-copy">Founder-managed DeepSeek is the default. Free uses V4 Flash; Builder can use V4 Flash or V4 Pro. Every response shows the resolved route.</p>
        <div class="managed-row">
          <div><strong>${activePersonal ? escapeHtml(activePersonal.name) : 'Founder managed'}</strong><span>${activePersonal ? `${escapeHtml(activePersonal.model)} &middot; outside managed quota` : `${escapeHtml(managedAlias.name)} &middot; ${escapeHtml(profile.label)}`}</span></div>
          <button class="secondary" type="button" data-action="selectModel">Quick switch</button>
        </div>
        <div class="choices">${modelButtons}</div>
      </section>
      <section class="section">
        <h2>Bring your own key</h2>
        <p class="section-copy">Add as many OpenAI-compatible or Ollama profiles as you need. Keys and custom headers use encrypted operating-system storage and never enter the project.</p>
        <div class="connections">${personalProfileRows}</div>
        <form class="profile-form" id="personal-profile-form">
          <input type="hidden" id="profile-id">
          <div class="form-title"><strong id="profile-form-title">Add personal AI</strong><span>Saved locally on this computer</span></div>
          <div class="form-grid">
            <div class="field"><label for="profile-kind">Connection</label><select id="profile-kind"><option value="openai-compatible">OpenAI-compatible</option><option value="ollama">Ollama on this computer</option></select></div>
            <div class="field"><label for="profile-name">Name</label><input id="profile-name" maxlength="60" required placeholder="My coding model"></div>
            <div class="field wide"><label for="profile-url">Base URL</label><input id="profile-url" required placeholder="https://provider.example/v1"></div>
            <div class="field"><label for="profile-model">Model ID</label><input id="profile-model" maxlength="200" required placeholder="provider-model-id"></div>
            <div class="field"><label for="profile-vision-model">Screenshot model</label><input id="profile-vision-model" maxlength="200" placeholder="Same as Model ID when blank"><span class="field-hint">Use a multimodal model that can read PNG, JPEG, or WebP.</span></div>
            <div class="field"><label for="profile-key">API key</label><input id="profile-key" type="password" autocomplete="off" required placeholder="Required for remote providers"><span class="field-hint">Leave blank while editing to keep the encrypted key.</span></div>
            <div class="field wide"><label for="profile-headers">Optional headers (JSON)</label><textarea id="profile-headers" spellcheck="false" placeholder='{"X-Organization": "team-id"}'></textarea><span class="field-hint">Leave blank while editing to keep existing encrypted headers.</span></div>
            <label class="check-field wide" for="profile-visuals"><input id="profile-visuals" type="checkbox"><span>Use this profile for screenshot reading. Only one Personal AI or Ollama profile can own screenshots at a time.</span></label>
          </div>
          <div class="button-row"><button class="primary" type="submit" id="profile-save">Save and use</button><button class="secondary" type="button" id="profile-cancel" hidden>Cancel edit</button></div>
        </form>
      </section>
    </div>

    <div class="panel" data-panel="infrastructure">
      <section class="section">
        <h2>Infrastructure mode</h2>
        <p class="section-copy">Choose where work runs and which connected services this workspace may use.</p>
        <div class="segments" role="group" aria-label="Infrastructure mode">${modeButtons}</div>
        <div class="mode-detail"><strong>${escapeHtml(modeDefinition.summary)}</strong><span>${escapeHtml(modeDefinition.services)}</span></div>
      </section>
      <section class="section">
        <h2>Local models</h2>
        <p class="section-copy">Ollama can run through the embedded Founder Node without sending prompts to a cloud model.</p>
        <div class="button-row"><button class="primary" type="button" data-open-ai-tab>Manage local AI</button></div>
      </section>
    </div>

    <div class="panel" data-panel="connections">
      <section class="section">
        <h2>Services</h2>
        <p class="section-copy">Connect the tools Founder can use to build and ship. Credentials remain managed by Founder OS rather than exposed in editor settings.</p>
        <div class="connections">${connectionRows}</div>
        <div class="button-row"><button class="primary" type="button" data-action="openConnections">Open connection center</button></div>
      </section>
    </div>

    <div class="panel" data-panel="advanced">
      <section class="section">
        <h2>Advanced controls</h2>
        <p class="section-copy">Editor preferences, raw connection overrides, and the local Node configuration.</p>
        <div class="button-row"><button class="secondary" type="button" data-action="openAdvancedSettings">Advanced settings</button><button class="secondary" type="button" data-action="openNodeConfig">Node configuration</button></div>
      </section>
      <section class="section">
        <h2>Daily quality review</h2>
        <p class="section-copy">Review the last 24 hours of Founder-owned work, run selected build and test tasks, probe safe health endpoints, and write an evidence report. The review waits whenever another Founder task is active and never edits or rolls back files.</p>
        <div class="connection-row">
          <div class="connection-copy"><strong>${dailyQualityEnabled ? 'Enabled' : 'Off'}</strong><span>${dailyQualityEnabled ? 'Runs at most once every 24 hours' : 'Opt in when you are ready'}</span></div>
          <span class="status-pill ${dailyQualityEnabled ? 'healthy' : 'neutral'}">${dailyQualityEnabled ? 'Daily' : 'Manual'}</span>
        </div>
        <div class="button-row"><button class="primary" type="button" data-action="runDailyQualityReview">Run review now</button><button class="secondary" type="button" data-action="toggleDailyQualityReview">${dailyQualityEnabled ? 'Disable daily review' : 'Enable daily review'}</button><button class="secondary" type="button" data-action="configureDailyQualityReview">Review settings</button></div>
      </section>
    </div>
  </main>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const personalProfiles = ${personalProfilesJson};
    const activeTab = ${activeTabJson};
    const tabs = [...document.querySelectorAll('[data-tab]')];
    const panels = [...document.querySelectorAll('[data-panel]')];
    const showTab = (name, notify = false) => {
      for (const item of tabs) item.classList.toggle('selected', item.dataset.tab === name);
      for (const panel of panels) panel.classList.toggle('selected', panel.dataset.panel === name);
      vscode.setState({ ...(vscode.getState() || {}), tab: name });
      if (notify) vscode.postMessage({ type: 'selectTab', tab: name });
    };
    showTab(activeTab);
    for (const tab of tabs) {
      tab.addEventListener('click', () => showTab(tab.dataset.tab, true));
    }
    for (const button of document.querySelectorAll('[data-action]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'action', action: button.dataset.action }));
    }
    for (const button of document.querySelectorAll('[data-mode]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'selectMode', mode: button.dataset.mode }));
    }
    for (const button of document.querySelectorAll('[data-alias]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'selectManagedAlias', alias: button.dataset.alias }));
    }
    for (const button of document.querySelectorAll('[data-open-ai-tab]')) {
      button.addEventListener('click', () => showTab('ai', true));
    }
    for (const button of document.querySelectorAll('[data-personal-select]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'selectPersonalProfile', profileId: button.dataset.personalSelect }));
    }
    for (const button of document.querySelectorAll('[data-personal-test]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'testPersonalProfile', profileId: button.dataset.personalTest }));
    }
    for (const button of document.querySelectorAll('[data-personal-toggle]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'togglePersonalProfile', profileId: button.dataset.personalToggle, enabled: button.dataset.enabled === 'true' }));
    }
    for (const button of document.querySelectorAll('[data-personal-delete]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'deletePersonalProfile', profileId: button.dataset.personalDelete }));
    }
    const profileForm = document.getElementById('personal-profile-form');
    const profileId = document.getElementById('profile-id');
    const profileKind = document.getElementById('profile-kind');
    const profileName = document.getElementById('profile-name');
    const profileUrl = document.getElementById('profile-url');
    const profileModel = document.getElementById('profile-model');
    const profileVisionModel = document.getElementById('profile-vision-model');
    const profileVisuals = document.getElementById('profile-visuals');
    const profileKey = document.getElementById('profile-key');
    const profileHeaders = document.getElementById('profile-headers');
    const profileTitle = document.getElementById('profile-form-title');
    const profileCancel = document.getElementById('profile-cancel');
    const resetForm = () => {
      profileForm.reset();
      profileId.value = '';
      profileTitle.textContent = 'Add personal AI';
      profileCancel.hidden = true;
    };
    profileKind.addEventListener('change', () => {
      if (profileKind.value === 'ollama') {
        if (!profileUrl.value) profileUrl.value = 'http://127.0.0.1:11434';
        profileKey.required = false;
        profileKey.placeholder = 'Optional';
      } else {
        profileKey.required = !profileId.value;
        profileKey.placeholder = 'Required for remote providers';
      }
    });
    for (const button of document.querySelectorAll('[data-personal-edit]')) {
      button.addEventListener('click', () => {
        const profile = personalProfiles.find((candidate) => candidate.id === button.dataset.personalEdit);
        if (!profile) return;
        showTab('ai', true);
        profileId.value = profile.id;
        profileKind.value = profile.kind;
        profileName.value = profile.name;
        profileUrl.value = profile.baseUrl;
        profileModel.value = profile.model;
        profileVisionModel.value = profile.visionModel || profile.model;
        profileVisuals.checked = profile.useForVisuals === true;
        profileKey.value = '';
        profileKey.required = false;
        profileHeaders.value = '';
        profileTitle.textContent = 'Edit ' + profile.name;
        profileCancel.hidden = false;
        profileName.focus();
      });
    }
    profileCancel.addEventListener('click', resetForm);
    profileForm.addEventListener('submit', (event) => {
      event.preventDefault();
      vscode.setState({ ...(vscode.getState() || {}), tab: 'ai' });
      vscode.postMessage({
        type: 'savePersonalProfile',
        draft: {
          id: profileId.value || undefined,
          kind: profileKind.value,
          name: profileName.value,
          baseUrl: profileUrl.value,
          model: profileModel.value,
          visionModel: profileVisionModel.value,
          useForVisuals: profileVisuals.checked,
          apiKey: profileKey.value,
          headers: profileHeaders.value,
        },
      });
    });
  </script>
</body>
</html>`;
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function isFounderSettingsTab(value: unknown): value is FounderSettingsTab {
  return value === 'account'
    || value === 'ai'
    || value === 'infrastructure'
    || value === 'connections'
    || value === 'advanced';
}

export function normalizeFounderSettingsTab(value: unknown): FounderSettingsTab {
  return isFounderSettingsTab(value) ? value : 'account';
}

function personalDraftFromMessage(
  value: NonNullable<FounderSettingsMessage['draft']>,
): PersonalAiProfileDraft {
  const kind = value.kind === 'ollama' ? 'ollama' : 'openai-compatible';
  const headers = value.headers?.trim()
    ? parsePersonalAiHeaders(value.headers)
    : value.id
      ? undefined
      : {};
  return {
    id: value.id?.trim() || undefined,
    name: value.name ?? '',
    kind,
    baseUrl: value.baseUrl ?? '',
    apiKey: value.apiKey?.trim() || undefined,
    model: value.model ?? '',
    visionModel: value.visionModel?.trim() || undefined,
    useForVisuals: value.useForVisuals === true,
    headers,
  };
}

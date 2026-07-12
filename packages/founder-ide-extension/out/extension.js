"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
/**
 * Activation entry point for the Founder OS chat extension.
 *
 * On activation:
 *   1. Resolve credentials (settings → `~/FounderVault/node-config.json`).
 *   2. Register the `LanguageModelChatProvider` + agentic tools + chat participant.
 *   3. Wire the execution-profile selector (status bar + QuickPick).
 *   4. Wire the DDollar cost tracker (status bar + breakdown).
 *   5. If creds missing, show a "not paired" notification + status bar.
 *   6. Always wire commands and a status bar item so the user can re-pair
 *      without reloading.
 */
const vscode = __importStar(require("vscode"));
const fs = __importStar(require("node:fs"));
const os = __importStar(require("node:os"));
const path = __importStar(require("node:path"));
const credentials_1 = require("./credentials");
const chat_provider_1 = require("./chat-provider");
const models_1 = require("./models");
const edit_file_1 = require("./tools/edit-file");
const run_command_1 = require("./tools/run-command");
const read_workspace_1 = require("./tools/read-workspace");
const profile_manager_1 = require("./profile-manager");
const cost_tracker_1 = require("./cost-tracker");
const chat_participant_1 = require("./chat-participant");
const debug_squasher_status_1 = require("./debug-squasher-status");
let connectionStatusBar;
let registeredProvider;
let registeredParticipant;
let profileManager;
let costTracker;
let debugSquasherDisposable;
let currentCreds = null;
function activate(context) {
    // Status bar (connection state) ----------------------------------------------------
    connectionStatusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    connectionStatusBar.command = 'founderOs.manage';
    context.subscriptions.push(connectionStatusBar);
    // Execution-profile selector + DDollar cost tracker (independent of creds) ---------
    profileManager = new profile_manager_1.ProfileManager(context);
    costTracker = new cost_tracker_1.CostTracker();
    context.subscriptions.push(profileManager, costTracker);
    profileManager.show();
    costTracker.show();
    // Agentic tools (registered once; available to any chat participant / model).
    // `vscode.lm.registerTool` only exists on VS Code 1.96+ (proposed `lmTools`
    // API, later stable). Guard so activation does not crash on 1.93.1, where the
    // chat participant (stable API) is the primary surface. Even where the
    // function exists, registration fails if the `languageModelTools` contribution
    // point isn't processed (proposed-gated on 1.93.1), so we swallow that case.
    if (typeof vscode.lm.registerTool === 'function') {
        const tools = [
            ['founder.editFile', edit_file_1.editFileTool],
            ['founder.runCommand', run_command_1.runCommandTool],
            ['founder.readWorkspace', read_workspace_1.readWorkspaceTool],
        ];
        for (const [name, tool] of tools) {
            try {
                context.subscriptions.push(vscode.lm.registerTool(name, tool));
            }
            catch {
                // Tool contribution point not processed on this VS Code build — skip.
            }
        }
    }
    // Commands -------------------------------------------------------------------------
    context.subscriptions.push(vscode.commands.registerCommand('founderOs.manage', () => manageConnection(context)), vscode.commands.registerCommand('founderOs.pair', () => pairWithFounderNode(context)), vscode.commands.registerCommand('founderOs.connectFounderOs', () => connectFounderOsAccount(context)), vscode.commands.registerCommand('founderOs.openVaultConfig', openVaultConfig), vscode.commands.registerCommand('founderOs.selectModel', selectModelAlias), vscode.commands.registerCommand('founderOs.selectProfile', () => profileManager?.selectProfile()), vscode.commands.registerCommand('founderOs.showCostBreakdown', () => costTracker?.showBreakdown()), vscode.commands.registerCommand('founderOs.resetCost', () => {
        costTracker?.reset();
        void vscode.window.showInformationMessage('Founder OS DDollar session counter reset.');
    }));
    // First-pass registration (synchronous so the model picker populates fast).
    registerOrNotify(context);
    // Auto-load ~/FounderVault/node-config.json into founderOs.* settings so
    // pairing isn't manual every session. Fire-and-forget; re-registers after.
    void (0, credentials_1.syncVaultIntoSettings)().then((synced) => {
        if (synced)
            registerOrNotify(context);
    });
    // Re-resolve when relevant settings change.
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('founderOs')) {
            registerOrNotify(context);
        }
    }));
    // Re-resolve when the vault file appears / changes (pairing completed while
    // the editor is open). We watch the FounderVault directory if it exists.
    watchVaultFile(context, () => {
        void (0, credentials_1.syncVaultIntoSettings)().then(() => registerOrNotify(context));
    });
}
function deactivate() {
    registeredProvider?.dispose();
    registeredParticipant?.dispose();
    connectionStatusBar?.dispose();
    profileManager?.dispose();
    costTracker?.dispose();
    debugSquasherDisposable?.dispose();
}
/** Register the chat provider + participant if we have creds; otherwise show "not paired". */
function registerOrNotify(context) {
    const creds = (0, credentials_1.resolveCredentials)();
    if (!creds) {
        registeredProvider?.dispose();
        registeredProvider = undefined;
        registeredParticipant?.dispose();
        registeredParticipant = undefined;
        debugSquasherDisposable?.dispose();
        debugSquasherDisposable = undefined;
        currentCreds = null;
        setStatusNotPaired();
        void showPairPrompt(context);
        return;
    }
    if (currentCreds &&
        currentCreds.apiBaseUrl === creds.apiBaseUrl &&
        currentCreds.nodeId === creds.nodeId &&
        currentCreds.nodeToken === creds.nodeToken &&
        registeredProvider) {
        // Already registered with identical creds — just refresh the label.
        setStatusConnected(currentCreds);
        return;
    }
    // Credentials changed — re-register.
    registeredProvider?.dispose();
    registeredParticipant?.dispose();
    debugSquasherDisposable?.dispose();
    const provider = new chat_provider_1.FounderOsChatProvider(creds, {
        onRequestStart: (modelId) => {
            connectionStatusBar.text = `$(sync~spin) Founder OS: ${modelId}`;
            connectionStatusBar.tooltip = 'Streaming response from Founder OS gateway…';
        },
        onMetadata: (meta) => {
            const tier = meta.tier ?? '?';
            const cost = typeof meta.ddollarCost === 'number' ? `${meta.ddollarCost} D$` : '';
            const provider2 = meta.provider ?? '';
            const model = meta.model ?? '';
            connectionStatusBar.text = `$(sparkle) Founder OS: ${tier}${cost ? ` · ${cost}` : ''}`;
            connectionStatusBar.tooltip = `Last route — tier: ${tier}, provider: ${provider2}, model: ${model}, cost: ${cost || 'n/a'}`;
            costTracker?.record(meta);
        },
        onRequestEnd: (_modelId, ok, errorMessage) => {
            if (ok) {
                connectionStatusBar.text = '$(check) Founder OS: Connected';
                connectionStatusBar.tooltip = `Founder OS gateway connected. Creds source: ${creds.source}.`;
            }
            else if (errorMessage) {
                connectionStatusBar.text = '$(error) Founder OS: Error';
                connectionStatusBar.tooltip = `Last request failed: ${errorMessage}`;
            }
        },
    });
    // `vscode.lm.registerLanguageModelChatProvider` only exists on VS Code 1.96+
    // (the `LanguageModelChatProvider` API). On 1.93.1 this function is absent, so
    // we skip provider registration and rely on the chat participant (stable API)
    // which streams directly from the gateway. Guarding here keeps activation alive.
    if (typeof vscode.lm.registerLanguageModelChatProvider === 'function') {
        registeredProvider = vscode.lm.registerLanguageModelChatProvider(models_1.FOUNDER_OS_VENDOR, provider);
        context.subscriptions.push(registeredProvider);
    }
    else {
        registeredProvider = undefined;
    }
    // Enhanced chat participant — drives a real vscode.lm round-trip with
    // Memory Engine injection + tool use. Falls back to onboarding only if the
    // participant id is already claimed by another extension.
    registeredParticipant = (0, chat_participant_1.registerFounderOsChatParticipant)(context, {
        creds,
        profileManager: profileManager,
        costTracker: costTracker,
    });
    // Debug Squasher status bar — polls /api/debug-squasher/latest every 2 min.
    debugSquasherDisposable = (0, debug_squasher_status_1.createDebugSquasherStatus)(context, () => (0, credentials_1.resolveCredentials)());
    context.subscriptions.push(debugSquasherDisposable);
    currentCreds = creds;
    setStatusConnected(creds);
}
function setStatusConnected(creds) {
    if (!connectionStatusBar)
        return;
    connectionStatusBar.text = '$(check) Founder OS: Connected';
    connectionStatusBar.tooltip = `Connected to ${creds.apiBaseUrl} (creds: ${creds.source}). Click to manage.`;
    connectionStatusBar.show();
}
function setStatusNotPaired() {
    if (!connectionStatusBar)
        return;
    connectionStatusBar.text = '$(warning) Founder OS: Not Paired';
    connectionStatusBar.tooltip =
        'Founder Node credentials not found. Pair Founder Node, or set founderOs.apiBaseUrl / nodeId / nodeToken.';
    connectionStatusBar.show();
}
let pairPromptShownThisSession = false;
async function showPairPrompt(context) {
    if (pairPromptShownThisSession)
        return;
    pairPromptShownThisSession = true;
    const choice = await vscode.window.showWarningMessage('Founder OS chat: Founder Node not paired. Pair it to enable Founder OS models in Chat.', 'Pair Founder Node', 'Open settings', 'Dismiss');
    if (choice === 'Pair Founder Node') {
        await pairWithFounderNode(context);
    }
    else if (choice === 'Open settings') {
        void vscode.commands.executeCommand('workbench.action.openSettings', 'founderOs');
    }
}
async function manageConnection(context) {
    const items = [
        {
            label: 'Connect Founder OS (Twitter)…',
            description: 'open doxxedcrypto.digital login — identity syncs via Founder Node',
            action: () => connectFounderOsAccount(context),
        },
        {
            label: 'Load Founder Node from vault…',
            description: (0, credentials_1.vaultFileExists)() ? 'vault file present — no pairing code needed' : 'vault file missing',
            action: () => pairWithFounderNode(context),
        },
        {
            label: 'Open settings (founderOs.*)',
            description: 'override apiBaseUrl / nodeId / nodeToken',
            action: () => vscode.commands.executeCommand('workbench.action.openSettings', 'founderOs'),
        },
        {
            label: 'Open node-config.json',
            description: (0, credentials_1.nodeConfigPath)(),
            action: () => openVaultConfig(),
        },
        {
            label: 'Select model alias',
            description: 'change the default Founder OS model',
            action: () => selectModelAlias(),
        },
        {
            label: 'Select execution profile…',
            description: 'Turbo / Balanced / Architect / Autonomous',
            action: () => profileManager?.selectProfile(),
        },
        {
            label: 'Reset DDollar session counter',
            description: 'clear the session spend display',
            action: () => {
                costTracker?.reset();
            },
        },
    ];
    const picked = await vscode.window.showQuickPick(items, {
        placeHolder: 'Founder OS — manage connection',
    });
    if (picked)
        await picked.action();
}
/**
 * Twitter / Founder OS identity lives on doxxedcrypto.digital.
 * Once the account is linked to Founder Node (heartbeat), the IDE reads
 * ~/FounderVault/node-config.json — no Skycode GitHub/Google/Apple and no
 * manual pairing-code paste in the IDE.
 */
async function connectFounderOsAccount(context) {
    const hasVault = (0, credentials_1.vaultFileExists)();
    const choice = await vscode.window.showInformationMessage(hasVault
        ? 'Founder Node vault is already on this machine. Sign in with Twitter on Founder OS only if you need to manage your cloud account. AI in this IDE uses the Node token — not Skycode cloud login.'
        : 'Sign in to Founder OS with Twitter, then open Founder Node on this PC so it can write ~/FounderVault/node-config.json. After that, Founder IDE loads credentials automatically (no pairing code in the IDE).', 'Open Twitter login', 'Open Builder settings', hasVault ? 'Reload vault now' : 'Cancel');
    if (choice === 'Open Twitter login') {
        void vscode.env.openExternal(vscode.Uri.parse('https://doxxedcrypto.digital/login?callbackUrl=/settings/builder'));
    }
    else if (choice === 'Open Builder settings') {
        void vscode.env.openExternal(vscode.Uri.parse('https://doxxedcrypto.digital/settings/builder'));
    }
    else if (choice === 'Reload vault now') {
        const synced = await (0, credentials_1.syncVaultIntoSettings)();
        registerOrNotify(context);
        void vscode.window.showInformationMessage(synced
            ? `Vault loaded — node ${synced.nodeId} @ ${synced.apiBaseUrl}`
            : 'Vault file missing or invalid.');
    }
}
async function pairWithFounderNode(context) {
    const file = (0, credentials_1.nodeConfigPath)();
    // Prefer vault auto-load — Founder Node writes this on pair / Connect IDE.
    if ((0, credentials_1.vaultFileExists)()) {
        const synced = await (0, credentials_1.syncVaultIntoSettings)();
        if (synced) {
            registerOrNotify(context);
            const open = await vscode.window.showInformationMessage(`Loaded Founder Node credentials from:\n${file}\n\napiBaseUrl=${synced.apiBaseUrl}\nnodeId=${synced.nodeId}`, 'Reload window', 'Open file', 'OK');
            if (open === 'Open file')
                await openVaultConfig();
            else if (open === 'Reload window')
                void vscode.commands.executeCommand('workbench.action.reloadWindow');
            return;
        }
    }
    // Optional: paste a Twitter-auth session JWT + API base for cloud-only pairing
    // when Founder Node isn't installed yet (thin slice — nodeId/token still preferred).
    const choice = await vscode.window.showInformationMessage('Founder OS chat needs a paired Founder Node.\n\nOpen Founder Node on this machine and click "Connect IDE / Pair". This writes ~/FounderVault/node-config.json, which the extension loads automatically.', 'Open Founder OS settings', 'Paste API + node credentials', 'Open docs', 'Cancel');
    if (choice === 'Open Founder OS settings') {
        void vscode.commands.executeCommand('workbench.action.openSettings', 'founderOs');
    }
    else if (choice === 'Paste API + node credentials') {
        const apiBaseUrl = await vscode.window.showInputBox({
            prompt: 'Founder OS API base URL',
            value: 'https://doxxedcrypto.digital',
            placeHolder: 'https://doxxedcrypto.digital',
        });
        if (!apiBaseUrl)
            return;
        const nodeId = await vscode.window.showInputBox({
            prompt: 'Founder Node ID (from Settings → Founder Stack after pairing)',
            placeHolder: 'fn_…',
        });
        if (!nodeId)
            return;
        const nodeToken = await vscode.window.showInputBox({
            prompt: 'Founder Node token (shown once at pair time, or from node-config.json)',
            password: true,
        });
        if (!nodeToken)
            return;
        const cfg = vscode.workspace.getConfiguration('founderOs');
        await cfg.update('apiBaseUrl', apiBaseUrl.replace(/\/$/, ''), vscode.ConfigurationTarget.Global);
        await cfg.update('nodeId', nodeId.trim(), vscode.ConfigurationTarget.Global);
        await cfg.update('nodeToken', nodeToken.trim(), vscode.ConfigurationTarget.Global);
        registerOrNotify(context);
        void vscode.window.showInformationMessage('Founder OS credentials saved to User settings.');
    }
    else if (choice === 'Open docs') {
        void vscode.env.openExternal(vscode.Uri.parse('https://doxxedcrypto.digital/downloads#founder-node'));
    }
}
async function openVaultConfig() {
    const file = (0, credentials_1.nodeConfigPath)();
    if (!fs.existsSync(file)) {
        const choice = await vscode.window.showWarningMessage(`No node-config.json at ${file}. Pair Founder Node first.`, 'Pair Founder Node', 'Cancel');
        if (choice === 'Pair Founder Node') {
            void vscode.commands.executeCommand('founderOs.pair');
        }
        return;
    }
    const doc = await vscode.workspace.openTextDocument(file);
    await vscode.window.showTextDocument(doc);
}
async function selectModelAlias() {
    const items = models_1.FOUNDER_OS_MODELS.map((m) => ({
        label: m.name,
        description: m.id,
        detail: m.detail,
        picked: m.isDefault,
    }));
    const picked = await vscode.window.showQuickPick(items, {
        placeHolder: 'Select a Founder OS model alias (changes the chat model dropdown selection)',
    });
    if (!picked)
        return;
    // Open the chat model picker so the user can apply the selection. The
    // underlying provider is already registered; this is a UX hint.
    void vscode.window.showInformationMessage(`Selected ${picked.label}. Pick it from the model dropdown in Chat to apply.`);
}
/**
 * Watch `~/FounderVault/node-config.json` for create/change so pairing done
 * while the editor is open is picked up without a manual reload. Best-effort:
 * if the `FounderVault` directory doesn't exist yet, we fall back to watching
 * the home directory one level up and re-check on each event.
 */
function watchVaultFile(context, onChange) {
    const file = (0, credentials_1.nodeConfigPath)();
    const dir = path.dirname(file);
    let pattern;
    try {
        if (!fs.existsSync(dir)) {
            // Watch the home dir for the FounderVault folder appearing at all.
            const home = vscode.Uri.file(os.homedir()).fsPath;
            pattern = new vscode.RelativePattern(vscode.Uri.file(home), 'FounderVault/node-config.json');
        }
        else {
            pattern = new vscode.RelativePattern(vscode.Uri.file(dir), 'node-config.json');
        }
    }
    catch {
        return;
    }
    try {
        const watcher = vscode.workspace.createFileSystemWatcher(pattern, false, false, false);
        context.subscriptions.push(watcher);
        context.subscriptions.push(watcher.onDidCreate(() => onChange()), watcher.onDidChange(() => onChange()));
    }
    catch {
        // Filesystem watching can fail on some platforms / network drives. Non-fatal.
    }
}
//# sourceMappingURL=extension.js.map
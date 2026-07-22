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
exports.FounderShortcutRegistry = void 0;
const vscode = __importStar(require("vscode"));
const credentials_1 = require("./credentials");
const founder_shortcuts_state_1 = require("./founder-shortcuts-state");
const founder_hub_state_1 = require("./founder-hub-state");
class FounderShortcutItem extends vscode.TreeItem {
    constructor(entry) {
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
class FounderShortcutTreeProvider {
    surface;
    changeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.changeEmitter.event;
    constructor(surface) {
        this.surface = surface;
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        return (0, founder_shortcuts_state_1.shortcutEntries)(this.surface, currentShortcutState()).map((entry) => new FounderShortcutItem(entry));
    }
    refresh() {
        this.changeEmitter.fire();
    }
    dispose() {
        this.changeEmitter.dispose();
    }
}
class FounderShortcutRegistry {
    providers = [];
    views = [];
    constructor() {
        for (const surface of founder_shortcuts_state_1.FOUNDER_SHORTCUT_SURFACES) {
            const provider = new FounderShortcutTreeProvider(surface);
            const view = vscode.window.createTreeView(founder_shortcuts_state_1.FOUNDER_SHORTCUT_VIEW_IDS[surface], {
                treeDataProvider: provider,
                showCollapseAll: false,
            });
            this.providers.push(provider);
            this.views.push(view);
        }
        this.refresh();
    }
    refresh() {
        const connected = Boolean((0, credentials_1.resolveCredentials)());
        for (const provider of this.providers)
            provider.refresh();
        const nodeView = this.views[founder_shortcuts_state_1.FOUNDER_SHORTCUT_SURFACES.indexOf('node')];
        nodeView.badge = connected
            ? { value: 1, tooltip: 'Founder Node connected' }
            : undefined;
    }
    dispose() {
        for (const provider of this.providers)
            provider.dispose();
        for (const view of this.views)
            view.dispose();
        this.providers.length = 0;
        this.views.length = 0;
    }
}
exports.FounderShortcutRegistry = FounderShortcutRegistry;
function currentShortcutState() {
    const config = vscode.workspace.getConfiguration('founderOs');
    const mode = (0, founder_hub_state_1.normalizeWorkspaceMode)(config.get('workspaceMode'));
    return {
        connected: Boolean((0, credentials_1.resolveCredentials)()),
        workspaceName: vscode.workspace.name?.trim()
            || vscode.workspace.workspaceFolders?.[0]?.name
            || 'Open a workspace',
        modeLabel: (0, founder_hub_state_1.workspaceModeDefinition)(mode).label,
    };
}
//# sourceMappingURL=founder-shortcuts.js.map
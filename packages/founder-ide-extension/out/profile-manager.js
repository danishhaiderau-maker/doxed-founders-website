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
exports.ProfileManager = exports.EXECUTION_PROFILES = void 0;
exports.findProfile = findProfile;
exports.profileForAlias = profileForAlias;
/**
 * Execution-profile selector + persistence.
 *
 * Surfaces the four Founder OS execution profiles as a QuickPick + status-bar
 * menu. The chosen profile maps to a model alias (and the `X-Execution-Profile`
 * header the chat provider already sends). Selection is persisted in workspace
 * state so it survives reloads.
 *
 * Profiles (see task spec / design report §7):
 *   - Turbo      → founder-os-code      (speed + low DDollar cost)
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
const vscode = __importStar(require("vscode"));
const models_1 = require("./models");
exports.EXECUTION_PROFILES = [
    {
        id: 'turbo',
        label: 'Turbo',
        detail: 'Optimize for speed and low DDollar cost (founder-os-code).',
        aliasId: 'founder-os-code',
        icon: '$(rocket)',
    },
    {
        id: 'balanced',
        label: 'Balanced',
        detail: 'Default routing — let the Routing Engine decide (founder-os-auto).',
        aliasId: 'founder-os-auto',
        icon: '$(symbol-enum)',
    },
    {
        id: 'architect',
        label: 'Architect',
        detail: 'Prioritize deep reasoning (founder-os-reasoning).',
        aliasId: 'founder-os-reasoning',
        icon: '$(beaker)',
    },
    {
        id: 'autonomous',
        label: 'Autonomous',
        detail: 'Allow more expensive multi-step agent execution (maps to Architect for now).',
        aliasId: 'founder-os-reasoning',
        icon: '$(robot)',
    },
];
const DEFAULT_PROFILE = 'balanced';
const STATE_KEY = 'founderOs.executionProfile';
function findProfile(id) {
    return exports.EXECUTION_PROFILES.find((p) => p.id === id);
}
function profileForAlias(aliasId) {
    // Reverse lookup: which profile prefers this alias. founder-os-fast also
    // implies Turbo (cheap+fast), so we treat both code+fast as turbo-ish.
    if (aliasId === 'founder-os-reasoning') {
        return findProfile('architect');
    }
    if (aliasId === 'founder-os-auto') {
        return findProfile('balanced');
    }
    return findProfile('turbo');
}
class ProfileManager {
    context;
    bar;
    current;
    constructor(context) {
        this.context = context;
        this.bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
        this.bar.command = 'founderOs.selectProfile';
        context.subscriptions.push(this.bar);
        const stored = context.workspaceState.get(STATE_KEY);
        this.current = findProfile(stored ?? DEFAULT_PROFILE) ?? findProfile(DEFAULT_PROFILE);
    }
    get profile() {
        return this.current;
    }
    /** Model alias the active profile routes through. */
    get alias() {
        return (0, models_1.findModelAlias)(this.current.aliasId) ?? models_1.FOUNDER_OS_MODELS[0];
    }
    /** Show the status-bar item reflecting the active profile. */
    show() {
        this.bar.text = `${this.current.icon} ${this.current.label}`;
        this.bar.tooltip = `Founder OS execution profile: ${this.current.label}.\nModel: ${this.alias.id}.\nClick to change.`;
        this.bar.show();
    }
    /** Open the QuickPick and apply the selection. */
    async selectProfile() {
        const items = exports.EXECUTION_PROFILES.map((p) => ({
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
        if (!picked)
            return;
        await this.setProfile(picked.profile.id);
    }
    /** Programmatically set + persist the profile. */
    async setProfile(id) {
        const next = findProfile(id);
        if (!next)
            return;
        if (next.id === this.current.id) {
            this.show();
            return;
        }
        this.current = next;
        await this.context.workspaceState.update(STATE_KEY, next.id);
        this.show();
        // Best-effort backend persistence. No controller exists for this yet, so
        // we don't actually fire the request — flip `persistToBackend` when
        // `/api/routing-engine/profile` ships.
        void persistToBackend(next).catch(() => undefined);
        void vscode.window.showInformationMessage(`Founder OS profile set to ${next.label} (model: ${next.aliasId}).`);
    }
    dispose() {
        this.bar.dispose();
    }
}
exports.ProfileManager = ProfileManager;
/**
 * Stub for backend persistence. The `ExecutionProfileService` exists server-side
 * but has no HTTP controller yet — when one is added at `/api/routing-engine/profile`,
 * replace the body with a fetch that PUTs `{ workspaceId, profile }` using the
 * founder-node bearer. Kept as a no-op so the call site is already wired.
 */
async function persistToBackend(_profile) {
    return;
}
//# sourceMappingURL=profile-manager.js.map
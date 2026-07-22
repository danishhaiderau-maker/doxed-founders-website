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
exports.__testHooks = exports.FounderWorkspaceContextIndex = void 0;
const node_crypto_1 = require("node:crypto");
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const vscode = __importStar(require("vscode"));
const workspace_context_state_1 = require("./workspace-context-state");
const INDEXABLE_GLOB = '**/*.{ts,tsx,js,jsx,mjs,cjs,json,jsonc,md,mdx,py,go,rs,java,kt,swift,cs,cpp,c,h,hpp,css,scss,html,yml,yaml,toml,sql,prisma,sol,sh,ps1}';
const EXCLUDE_GLOB = '**/{.git,node_modules,dist,out,build,.next,.turbo,coverage,.cache,.venv,venv,__pycache__,artifacts}/**';
const MAX_FILES = 5_000;
const MAX_FILE_BYTES = 512_000;
const INITIAL_SYMBOL_BUDGET = 250;
const REFRESH_DEBOUNCE_MS = 1_500;
class FounderWorkspaceContextIndex {
    context;
    state = null;
    refreshPromise = null;
    refreshTimer;
    watcher;
    workspaceId;
    indexFile;
    constructor(context) {
        this.context = context;
        this.workspaceId = currentWorkspaceId();
        this.indexFile = this.indexFileFor(this.workspaceId);
        this.state = this.readPersisted(this.workspaceId, this.indexFile);
        this.watcher = vscode.workspace.createFileSystemWatcher(INDEXABLE_GLOB);
        context.subscriptions.push(this.watcher, this.watcher.onDidCreate(() => this.scheduleRefresh()), this.watcher.onDidChange(() => this.scheduleRefresh()), this.watcher.onDidDelete(() => this.scheduleRefresh()), vscode.workspace.onDidChangeWorkspaceFolders(() => {
            this.switchWorkspace();
            this.scheduleRefresh(true);
        }));
        void this.refresh();
    }
    contextFor(prompt) {
        return (0, workspace_context_state_1.formatWorkspaceContextForPrompt)(this.state, prompt);
    }
    summary() {
        return {
            files: this.state?.files.length ?? 0,
            symbols: this.state?.files.reduce((sum, file) => sum + file.symbols.length, 0) ?? 0,
            indexedAt: this.state?.indexedAt ?? null,
            refreshing: this.refreshPromise !== null,
        };
    }
    async refresh(force = false) {
        if (this.refreshPromise)
            return this.refreshPromise;
        this.refreshPromise = this.rebuild(force).finally(() => {
            this.refreshPromise = null;
        });
        return this.refreshPromise;
    }
    dispose() {
        if (this.refreshTimer)
            clearTimeout(this.refreshTimer);
        this.watcher.dispose();
    }
    scheduleRefresh(force = false) {
        if (this.refreshTimer)
            clearTimeout(this.refreshTimer);
        this.refreshTimer = setTimeout(() => void this.refresh(force), REFRESH_DEBOUNCE_MS);
        this.refreshTimer.unref?.();
    }
    indexFileFor(workspaceId) {
        return workspaceId
            ? path.join(this.context.globalStorageUri.fsPath, 'workspace-context', `${workspaceId}.json`)
            : null;
    }
    switchWorkspace() {
        const workspaceId = currentWorkspaceId();
        if (workspaceId === this.workspaceId)
            return;
        this.workspaceId = workspaceId;
        this.indexFile = this.indexFileFor(workspaceId);
        this.state = this.readPersisted(workspaceId, this.indexFile);
    }
    readPersisted(workspaceId, indexFile) {
        if (!workspaceId || !indexFile)
            return null;
        try {
            return (0, workspace_context_state_1.parseWorkspaceContextIndex)(JSON.parse(fs.readFileSync(indexFile, 'utf8')), workspaceId);
        }
        catch {
            return null;
        }
    }
    async rebuild(force) {
        this.switchWorkspace();
        const workspaceId = currentWorkspaceId();
        const indexFile = this.indexFile;
        if (!workspaceId || !indexFile)
            return;
        const uris = await vscode.workspace.findFiles(INDEXABLE_GLOB, EXCLUDE_GLOB, MAX_FILES);
        const previous = new Map(this.state?.files.map((file) => [file.path, file]) ?? []);
        const next = [];
        const symbolQueue = [];
        let symbolBudget = INITIAL_SYMBOL_BUDGET;
        for (const uri of uris) {
            const relativePath = relativeWorkspacePath(uri);
            if (!relativePath)
                continue;
            let stat;
            try {
                stat = await vscode.workspace.fs.stat(uri);
            }
            catch {
                continue;
            }
            if (stat.size > MAX_FILE_BYTES)
                continue;
            const old = previous.get(relativePath);
            if (!force && !(0, workspace_context_state_1.workspaceContextFileNeedsRefresh)(old, {
                path: relativePath,
                size: stat.size,
                mtimeMs: stat.mtime,
            })) {
                next.push(old);
                continue;
            }
            let bytes;
            try {
                bytes = await vscode.workspace.fs.readFile(uri);
            }
            catch {
                continue;
            }
            const file = {
                path: relativePath,
                languageId: languageIdForPath(relativePath),
                size: stat.size,
                mtimeMs: stat.mtime,
                sha256: (0, node_crypto_1.createHash)('sha256').update(bytes).digest('hex'),
                symbols: [],
            };
            next.push(file);
            if (symbolBudget > 0 && supportsSymbols(relativePath)) {
                symbolQueue.push({ uri, file });
                symbolBudget -= 1;
            }
        }
        for (let offset = 0; offset < symbolQueue.length; offset += 8) {
            const batch = symbolQueue.slice(offset, offset + 8);
            await Promise.all(batch.map(async ({ uri, file }) => {
                file.symbols = await documentSymbols(uri);
            }));
        }
        if (workspaceId !== currentWorkspaceId() || indexFile !== this.indexFile) {
            this.scheduleRefresh(true);
            return;
        }
        this.state = (0, workspace_context_state_1.buildWorkspaceContextIndex)(workspaceId, next);
        this.writePersisted(this.state, indexFile);
    }
    writePersisted(state, indexFile) {
        try {
            fs.mkdirSync(path.dirname(indexFile), { recursive: true });
            const temp = `${indexFile}.${process.pid}.tmp`;
            fs.writeFileSync(temp, JSON.stringify(state), 'utf8');
            fs.renameSync(temp, indexFile);
        }
        catch {
            // Project context is an optimization and must not block chat.
        }
    }
}
exports.FounderWorkspaceContextIndex = FounderWorkspaceContextIndex;
function currentWorkspaceId() {
    const roots = vscode.workspace.workspaceFolders
        ?.map((folder) => path.resolve(folder.uri.fsPath).toLowerCase())
        .sort();
    if (!roots || roots.length === 0)
        return null;
    return (0, node_crypto_1.createHash)('sha256').update(roots.join('\n')).digest('hex').slice(0, 24);
}
function relativeWorkspacePath(uri) {
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        const relative = path.relative(folder.uri.fsPath, uri.fsPath);
        if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
            continue;
        }
        const normalized = relative.replaceAll('\\', '/');
        return (vscode.workspace.workspaceFolders?.length ?? 0) > 1
            ? `${folder.name}/${normalized}`
            : normalized;
    }
    return null;
}
function languageIdForPath(file) {
    const name = path.basename(file).toLowerCase();
    if (name === 'dockerfile')
        return 'dockerfile';
    const extension = path.extname(name).slice(1);
    return extension || 'text';
}
function supportsSymbols(file) {
    return /\.(?:ts|tsx|js|jsx|mjs|cjs|py|go|rs|java|kt|swift|cs|cpp|c|h|hpp|sol)$/i.test(file);
}
async function documentSymbols(uri) {
    try {
        const symbols = await vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider', uri);
        if (!symbols)
            return [];
        const names = [];
        const visit = (symbol) => {
            if (names.length >= 40)
                return;
            if (symbol.name && !names.includes(symbol.name))
                names.push(symbol.name);
            if ('children' in symbol) {
                for (const child of symbol.children.slice(0, 12))
                    visit(child);
            }
        };
        for (const symbol of symbols)
            visit(symbol);
        return names;
    }
    catch {
        return [];
    }
}
exports.__testHooks = {
    languageIdForPath,
    supportsSymbols,
};
//# sourceMappingURL=workspace-context-index.js.map
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
exports.readWorkspaceTool = void 0;
/**
 * `founder-read-workspace` — LanguageModelTool.
 *
 * Walks the current workspace file tree and returns a compact summary the model
 * can reason about: the tree (respecting common ignore patterns) plus optional
 * file contents for a small set of explicitly-requested files.
 *
 * See design report §4.3 / §8.4.
 */
const vscode = __importStar(require("vscode"));
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const DEFAULT_IGNORE_DIRS = new Set([
    'node_modules',
    '.git',
    '.svn',
    '.hg',
    'out',
    'dist',
    'build',
    '.next',
    '.cache',
    '.turbo',
    'coverage',
    '__pycache__',
    '.venv',
    'venv',
    '.tmp',
    '.vscode-test',
]);
const DEFAULT_IGNORE_FILE_GLOBS = [
    /\.log$/i,
    /\.pyc$/i,
    /\.map$/i,
];
function shouldIgnoreName(name, isDir) {
    if (isDir && DEFAULT_IGNORE_DIRS.has(name))
        return true;
    if (!isDir && DEFAULT_IGNORE_FILE_GLOBS.some((re) => re.test(name)))
        return true;
    return false;
}
function walk(root, maxEntries) {
    const entries = [];
    let truncated = false;
    function recurse(dir, depth) {
        if (truncated)
            return;
        if (depth > 6)
            return;
        let names;
        try {
            names = fs.readdirSync(dir, { withFileTypes: true })
                .filter((d) => !shouldIgnoreName(d.name, d.isDirectory()))
                .map((d) => d.name);
        }
        catch {
            return;
        }
        names.sort();
        for (const name of names) {
            if (entries.length >= maxEntries) {
                truncated = true;
                return;
            }
            const full = path.join(dir, name);
            let stat;
            try {
                stat = fs.lstatSync(full);
            }
            catch {
                continue;
            }
            if (stat.isSymbolicLink())
                continue;
            const isDir = stat.isDirectory();
            const rel = path.relative(root, full).replace(/\\/g, '/');
            entries.push(isDir ? `${rel}/` : rel);
            if (isDir)
                recurse(full, depth + 1);
        }
    }
    recurse(root, 0);
    return { entries, truncated };
}
function resolveSubdir(subdir) {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root)
        return null;
    const candidate = !subdir
        ? path.resolve(root)
        : path.isAbsolute(subdir)
            ? path.resolve(subdir)
            : path.resolve(root, subdir);
    const relative = path.relative(path.resolve(root), candidate);
    if (relative.startsWith('..') || path.isAbsolute(relative))
        return null;
    try {
        const realRoot = fs.realpathSync(root);
        const realCandidate = fs.realpathSync(candidate);
        const realRelative = path.relative(realRoot, realCandidate);
        return realRelative.startsWith('..') || path.isAbsolute(realRelative)
            ? null
            : realCandidate;
    }
    catch {
        return null;
    }
}
function readWorkspaceFile(root, rel) {
    const full = path.isAbsolute(rel) ? path.resolve(rel) : path.resolve(root, rel);
    const relative = path.relative(path.resolve(root), full);
    if (relative.startsWith('..') || path.isAbsolute(relative))
        return null;
    try {
        if (fs.lstatSync(full).isSymbolicLink())
            return null;
        const realRoot = fs.realpathSync(root);
        const realFile = fs.realpathSync(full);
        const realRelative = path.relative(realRoot, realFile);
        if (realRelative.startsWith('..') || path.isAbsolute(realRelative))
            return null;
        const buf = fs.readFileSync(realFile, 'utf8');
        if (buf.length > 16_000) {
            return buf.slice(0, 16_000) + `\n…[truncated, ${buf.length - 16_000} more chars]`;
        }
        return buf;
    }
    catch {
        return null;
    }
}
exports.readWorkspaceTool = {
    async prepareInvocation(options, _token) {
        const sub = options.input.subdir ? ` under ${options.input.subdir}` : '';
        return {
            invocationMessage: `Reading workspace${sub}`,
        };
    },
    async invoke(options, _token) {
        const input = options.input;
        const root = resolveSubdir(input.subdir);
        if (!root) {
            return new vscode.LanguageModelToolResult([
                new vscode.LanguageModelTextPart('Error: no workspace folder is open.'),
            ]);
        }
        const maxEntries = Math.max(10, Math.min(5000, input.maxTreeEntries ?? 400));
        const { entries, truncated } = walk(root, maxEntries);
        const parts = [];
        parts.push(`Workspace root: ${root}`);
        parts.push(`File tree (${entries.length} entries${truncated ? ', truncated' : ''}):`);
        parts.push(entries.join('\n'));
        if (input.readFiles && input.readFiles.length > 0) {
            parts.push('\n--- File contents ---');
            for (const rel of input.readFiles.slice(0, 10)) {
                const content = readWorkspaceFile(root, rel);
                parts.push(`\n=== ${rel} ===`);
                parts.push(content ?? `(could not read ${rel})`);
            }
        }
        return new vscode.LanguageModelToolResult([
            new vscode.LanguageModelTextPart(parts.join('\n')),
        ]);
    },
};
//# sourceMappingURL=read-workspace.js.map
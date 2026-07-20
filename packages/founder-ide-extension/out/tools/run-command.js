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
exports.runCommandTool = void 0;
/**
 * `founder-run-command` — LanguageModelTool.
 *
 * Runs one reviewed command at a time and streams stdout/stderr into a visible
 * output channel while returning bounded output to the model.
 */
const vscode = __importStar(require("vscode"));
const path = __importStar(require("node:path"));
const fs = __importStar(require("node:fs"));
const node_child_process_1 = require("node:child_process");
let commandOutput = null;
let activeCommand = null;
function resolveCwd(cwd) {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root)
        return null;
    const candidate = !cwd
        ? path.resolve(root)
        : path.isAbsolute(cwd)
            ? path.resolve(cwd)
            : path.resolve(root, cwd);
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
function terminateCommand(child) {
    if (process.platform === 'win32' && child.pid) {
        const killer = (0, node_child_process_1.spawn)('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' });
        killer.unref();
        return;
    }
    child.kill('SIGTERM');
}
exports.runCommandTool = {
    async prepareInvocation(options, _token) {
        const cmd = options.input.command;
        return {
            invocationMessage: `Running: ${cmd}`,
            confirmationMessages: {
                title: 'Run terminal command',
                message: `Allow Founder OS to run this command?\n\n\`${cmd}\``,
            },
        };
    },
    async invoke(options, token) {
        const input = options.input;
        if (!input.command || typeof input.command !== 'string') {
            return new vscode.LanguageModelToolResult([
                new vscode.LanguageModelTextPart('Error: no command provided.'),
            ]);
        }
        const cwd = resolveCwd(input.cwd);
        if (!cwd) {
            return new vscode.LanguageModelToolResult([
                new vscode.LanguageModelTextPart('Error: command working directory must be inside the open workspace.'),
            ]);
        }
        if (activeCommand) {
            return new vscode.LanguageModelToolResult([
                new vscode.LanguageModelTextPart('Error: another Founder OS command is already running.'),
            ]);
        }
        const timeoutMs = Math.max(1000, Math.min(300_000, input.timeoutMs ?? 30_000));
        commandOutput ??= vscode.window.createOutputChannel('Founder OS');
        commandOutput.show(true);
        commandOutput.appendLine(`\n> ${input.command}`);
        commandOutput.appendLine(`  cwd: ${cwd}`);
        const output = [];
        const append = (text) => {
            output.push(text);
            commandOutput?.append(text);
        };
        const completion = await new Promise((resolve) => {
            const child = (0, node_child_process_1.spawn)(input.command, {
                cwd,
                shell: true,
                windowsHide: true,
                env: process.env,
            });
            activeCommand = child;
            let settled = false;
            let timeout;
            let cancellation;
            const finish = (value) => {
                if (settled)
                    return;
                settled = true;
                if (activeCommand === child)
                    activeCommand = null;
                if (timeout)
                    clearTimeout(timeout);
                cancellation?.dispose();
                resolve(value);
            };
            child.stdout?.on('data', (data) => append(data.toString('utf8')));
            child.stderr?.on('data', (data) => append(data.toString('utf8')));
            child.on('close', (code) => finish({ exitCode: code ?? 1 }));
            child.on('error', (error) => finish({ exitCode: 1, suffix: `\n[Founder OS: ${error.message}]` }));
            timeout = setTimeout(() => {
                terminateCommand(child);
                finish({
                    exitCode: 124,
                    suffix: `\n[Founder OS: command timed out after ${timeoutMs}ms]`,
                });
            }, timeoutMs);
            cancellation = token.onCancellationRequested(() => {
                terminateCommand(child);
                finish({ exitCode: 130, suffix: '\n[Founder OS: command cancelled]' });
            });
            if (token.isCancellationRequested) {
                terminateCommand(child);
                finish({ exitCode: 130, suffix: '\n[Founder OS: command cancelled]' });
            }
        });
        let body = `${output.join('')}${completion.suffix ?? ''}`;
        body += `\n[exit code ${completion.exitCode}]`;
        commandOutput.appendLine(`\n[exit code ${completion.exitCode}]`);
        // Truncate huge output so we don't blow the model's context window.
        const MAX = 20_000;
        if (body.length > MAX) {
            body = body.slice(-MAX);
            body = `…[truncated to last ${MAX} chars]\n${body}`;
        }
        return new vscode.LanguageModelToolResult([
            new vscode.LanguageModelTextPart(body || '(no output)'),
        ]);
    },
};
//# sourceMappingURL=run-command.js.map
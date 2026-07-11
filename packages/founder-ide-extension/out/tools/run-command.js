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
 * `founder.runCommand` — LanguageModelTool.
 *
 * Creates a terminal, sends the command text, and captures output by writing
 * stdout+stderr to a temp file and reading it back — the documented workaround
 * for the limited terminal-read API (see design report §4.3 / §8.4).
 *
 * The command is shown to the user in a real terminal so they can see what ran.
 * We append `> tmpfile 2>&1` to capture combined output, then read the temp file
 * and return it to the model as a `LanguageModelToolResult`.
 */
const vscode = __importStar(require("vscode"));
const os = __importStar(require("node:os"));
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
function isWindows() {
    return /^win/i.test(os.platform());
}
function makeTempOutputPath() {
    const dir = os.tmpdir();
    const name = `founder-os-cmd-${Date.now()}-${Math.round(Math.random() * 1e6)}.log`;
    return path.join(dir, name);
}
function resolveCwd(cwd) {
    if (!cwd) {
        return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    }
    if (path.isAbsolute(cwd))
        return cwd;
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    return root ? path.join(root, cwd) : cwd;
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
        const tmp = makeTempOutputPath();
        const cwd = resolveCwd(input.cwd);
        const timeoutMs = Math.max(1000, Math.min(300_000, input.timeoutMs ?? 30_000));
        // Wrap the user command so combined stdout+stderr lands in the temp file,
        // and append a unique sentinel so we know when the command finished.
        const sentinel = `__FOUNDER_OS_DONE_${Date.now()}__`;
        const redirect = isWindows()
            ? `> "${tmp}" 2>&1` // Windows cmd supports `> file 2>&1`
            : `> "${tmp}" 2>&1`;
        const doneMarker = isWindows()
            ? `echo ${sentinel} >> "${tmp}"`
            : `echo ${sentinel} >> "${tmp}"`;
        const fullCommand = `${input.command} ${redirect} ; ${doneMarker}`;
        const terminal = vscode.window.createTerminal({
            name: 'Founder OS',
            cwd,
        });
        terminal.show(true);
        terminal.sendText(fullCommand, true);
        // Poll the temp file for the sentinel up to timeoutMs.
        const start = Date.now();
        let output = '';
        let sawSentinel = false;
        while (Date.now() - start < timeoutMs) {
            if (token.isCancellationRequested) {
                break;
            }
            await sleep(300);
            try {
                if (fs.existsSync(tmp)) {
                    const raw = fs.readFileSync(tmp, 'utf8');
                    if (raw.includes(sentinel)) {
                        sawSentinel = true;
                        // Strip the sentinel line.
                        output = raw.replace(new RegExp(`\\s*${sentinel}\\s*$`), '');
                        break;
                    }
                }
            }
            catch {
                // file may be mid-write; keep polling.
            }
        }
        if (!sawSentinel && !token.isCancellationRequested) {
            // Timed out — read whatever is there and flag it.
            try {
                output = fs.existsSync(tmp) ? fs.readFileSync(tmp, 'utf8') : '';
            }
            catch {
                output = '';
            }
            output += `\n[Founder OS: command did not finish within ${timeoutMs}ms]`;
        }
        else if (token.isCancellationRequested) {
            output += `\n[Founder OS: command cancelled by user]`;
        }
        // Truncate huge output so we don't blow the model's context window.
        const MAX = 20_000;
        let body = output;
        if (body.length > MAX) {
            body = body.slice(0, MAX) + `\n…[truncated, ${output.length - MAX} more chars]`;
        }
        // Best-effort cleanup of the temp file.
        fs.promises.unlink(tmp).catch(() => undefined);
        return new vscode.LanguageModelToolResult([
            new vscode.LanguageModelTextPart(body || '(no output)'),
        ]);
    },
};
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
//# sourceMappingURL=run-command.js.map
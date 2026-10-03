"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.workspaceProjectId = workspaceProjectId;
exports.fetchMemoryContext = fetchMemoryContext;
exports.buildSystemPromptFromContext = buildSystemPromptFromContext;
exports.buildSystemPrompt = buildSystemPrompt;
const credentials_1 = require("./credentials");
/** Stable project id for the workspace — derived from the folder name + parent. */
function workspaceProjectId() {
    // Resolved lazily so we don't import vscode at module load (helps testability).
    const vscode = require('vscode');
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder)
        return undefined;
    // Use the workspace folder name; the server can resolve this to a real
    // project record via the Founder Node's userId. Keep it deterministic.
    return folder.name;
}
/** Fetch the memory context for the current workspace + user. Best-effort. */
async function fetchMemoryContext(creds, token) {
    const url = new URL(`${creds.apiBaseUrl.replace(/\/$/, '')}/api/memory/context`);
    const pid = workspaceProjectId();
    if (pid)
        url.searchParams.set('projectId', pid);
    const controller = new AbortController();
    const cancelSub = token.onCancellationRequested(() => controller.abort());
    // Hard cap so a slow memory endpoint never blocks chat for long.
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
        const res = await fetch(url.toString(), {
            method: 'GET',
            headers: {
                Authorization: (0, credentials_1.authorizationHeaderFromCredentials)(creds),
                Accept: 'application/json',
            },
            signal: controller.signal,
        });
        if (res.status === 404) {
            // Endpoint not implemented yet — expected on older deployments.
            return null;
        }
        if (!res.ok) {
            return null;
        }
        return (await res.json());
    }
    catch {
        // Network error / timeout / abort — treat as "no memory".
        return null;
    }
    finally {
        clearTimeout(timeout);
        cancelSub.dispose();
    }
}
/** Turn a memory entry's value into a single-line-ish summary string. */
function summarizeEntry(entry) {
    const key = entry.key ?? '(no key)';
    let valStr;
    if (typeof entry.value === 'string') {
        valStr = entry.value;
    }
    else {
        try {
            valStr = JSON.stringify(entry.value);
        }
        catch {
            valStr = String(entry.value);
        }
    }
    if (valStr.length > 800)
        valStr = valStr.slice(0, 800) + '…';
    return `- ${key}: ${valStr}`;
}
function summarizeBlock(label, entries) {
    if (!entries || entries.length === 0)
        return '';
    const lines = entries.slice(0, 30).map(summarizeEntry);
    return `## ${label}\n${lines.join('\n')}`;
}
/**
 * Build the system-prompt prefix to prepend to the messages array.
 * Returns `{ text: '' , hasMemory: false }` when no memory is available so the
 * caller can skip prepending anything.
 */
function buildSystemPromptFromContext(ctx) {
    if (!ctx) {
        return { text: '', hasMemory: false, skippedReason: 'no-context' };
    }
    const blocks = [];
    const project = summarizeBlock('Project memory', ctx.projectMemory);
    const founder = summarizeBlock('Founder memory', ctx.founderMemory);
    if (project)
        blocks.push(project);
    if (founder)
        blocks.push(founder);
    if (ctx.systemPromptHint && ctx.systemPromptHint.trim().length > 0) {
        blocks.push(ctx.systemPromptHint.trim());
    }
    if (blocks.length === 0) {
        return { text: '', hasMemory: false, skippedReason: 'empty' };
    }
    const header = 'You are Founder OS, an AI pair-programmer routed via the founder\'s own gateway. ' +
        'The following operational context is injected from the Memory Engine — use it to ' +
        'respect the founder\'s conventions, active task, and preferences.\n';
    return { text: `${header}\n${blocks.join('\n\n')}`, hasMemory: true };
}
/** Convenience: fetch + build in one call. Never throws. */
async function buildSystemPrompt(creds, token) {
    try {
        const ctx = await fetchMemoryContext(creds, token);
        return buildSystemPromptFromContext(ctx);
    }
    catch {
        return { text: '', hasMemory: false, skippedReason: 'fetch-error' };
    }
}
//# sourceMappingURL=memory.js.map
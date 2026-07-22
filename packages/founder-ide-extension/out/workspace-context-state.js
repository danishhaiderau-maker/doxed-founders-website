"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.WORKSPACE_CONTEXT_INDEX_VERSION = void 0;
exports.parseWorkspaceContextIndex = parseWorkspaceContextIndex;
exports.workspaceContextFileNeedsRefresh = workspaceContextFileNeedsRefresh;
exports.buildWorkspaceContextIndex = buildWorkspaceContextIndex;
exports.rankWorkspaceContextFiles = rankWorkspaceContextFiles;
exports.formatWorkspaceContextForPrompt = formatWorkspaceContextForPrompt;
exports.WORKSPACE_CONTEXT_INDEX_VERSION = 1;
const IMPORTANT_FILES = new Set([
    'agents.md',
    'package.json',
    'readme.md',
    'tsconfig.json',
    'pyproject.toml',
    'cargo.toml',
    'go.mod',
    'dockerfile',
]);
function terms(value) {
    return value
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .split(/\s+/)
        .filter((term) => term.length > 1);
}
function parseWorkspaceContextIndex(value, expectedWorkspaceId) {
    if (!value || typeof value !== 'object' || Array.isArray(value))
        return null;
    const candidate = value;
    if (candidate.version !== exports.WORKSPACE_CONTEXT_INDEX_VERSION ||
        candidate.workspaceId !== expectedWorkspaceId ||
        typeof candidate.indexedAt !== 'string' ||
        !Array.isArray(candidate.files)) {
        return null;
    }
    const files = candidate.files.filter((file) => Boolean(file &&
        typeof file.path === 'string' &&
        typeof file.languageId === 'string' &&
        Number.isFinite(file.size) &&
        Number.isFinite(file.mtimeMs) &&
        typeof file.sha256 === 'string' &&
        Array.isArray(file.symbols)));
    return { ...candidate, files };
}
function workspaceContextFileNeedsRefresh(previous, next) {
    return !previous || previous.size !== next.size || previous.mtimeMs !== next.mtimeMs;
}
function buildWorkspaceContextIndex(workspaceId, files, indexedAt = new Date().toISOString()) {
    return {
        version: exports.WORKSPACE_CONTEXT_INDEX_VERSION,
        workspaceId,
        indexedAt,
        files: [...files].sort((a, b) => a.path.localeCompare(b.path)),
    };
}
function fileScore(file, queryTerms) {
    const pathTerms = new Set(terms(file.path));
    const symbolTerms = new Set(file.symbols.flatMap(terms));
    let score = IMPORTANT_FILES.has(file.path.toLowerCase().split('/').at(-1) ?? '') ? 1 : 0;
    for (const term of queryTerms) {
        if (pathTerms.has(term))
            score += 5;
        if (symbolTerms.has(term))
            score += 3;
        if (file.path.toLowerCase().includes(term))
            score += 1;
    }
    return score;
}
function rankWorkspaceContextFiles(index, query, limit = 14) {
    const queryTerms = terms(query);
    return index.files
        .map((file) => ({ file, score: fileScore(file, queryTerms) }))
        .filter((entry) => entry.score > 0)
        .sort((a, b) => b.score - a.score || a.file.path.localeCompare(b.file.path))
        .slice(0, Math.max(1, limit))
        .map((entry) => entry.file);
}
function formatWorkspaceContextForPrompt(index, query, limit = 14) {
    if (!index || index.files.length === 0)
        return '';
    const ranked = rankWorkspaceContextFiles(index, query, limit);
    if (ranked.length === 0)
        return '';
    const symbolCount = index.files.reduce((sum, file) => sum + file.symbols.length, 0);
    const lines = ranked.map((file) => {
        const outline = file.symbols.length > 0
            ? ` :: ${file.symbols.slice(0, 8).join(', ')}`
            : '';
        return `- ${file.path} [${file.languageId}]${outline}`;
    });
    return [
        '## Founder local project map',
        `Indexed ${index.files.length} files and ${symbolCount} symbols. Source contents remain local.`,
        ...lines,
    ].join('\n');
}
//# sourceMappingURL=workspace-context-state.js.map
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
exports.nodeConfigPath = nodeConfigPath;
exports.readVaultConfig = readVaultConfig;
exports.resolveCredentials = resolveCredentials;
exports.normalizeApiBaseUrl = normalizeApiBaseUrl;
exports.syncVaultIntoSettings = syncVaultIntoSettings;
exports.proxyBaseUrl = proxyBaseUrl;
exports.bearerFromCredentials = bearerFromCredentials;
exports.authorizationHeaderFromCredentials = authorizationHeaderFromCredentials;
exports.vaultFileExists = vaultFileExists;
exports.clearFounderSession = clearFounderSession;
/**
 * Credential discovery for the Founder OS chat provider.
 *
 * The vault is authoritative for identity and credentials. Non-secret settings
 * may override the API URL; legacy node settings are accepted only when no
 * vault exists so older development installs can still migrate.
 *
 * The bearer token format is `fos_{nodeId}:{nodeToken}` (OpenAI-compat) or
 * `FounderNode {nodeId}:{nodeToken}` (preferred for gateway calls). See
 * `parseFounderNodeAuthHeader` in `@dcf/founder-vault` and `connect-ide.ts`.
 */
const fs = __importStar(require("node:fs"));
const os = __importStar(require("node:os"));
const path = __importStar(require("node:path"));
const vscode = __importStar(require("vscode"));
/** Path to the Founder Node vault config — `~/FounderVault/node-config.json`. */
function nodeConfigPath() {
    return path.join(os.homedir(), 'FounderVault', 'node-config.json');
}
function readVaultConfig() {
    const file = nodeConfigPath();
    try {
        if (!fs.existsSync(file))
            return null;
        const raw = fs.readFileSync(file, 'utf8');
        const parsed = JSON.parse(raw);
        if (typeof parsed.apiBaseUrl !== 'string' ||
            typeof parsed.nodeId !== 'string' ||
            typeof parsed.nodeToken !== 'string') {
            return null;
        }
        return parsed;
    }
    catch {
        return null;
    }
}
function nonEmpty(s) {
    return typeof s === 'string' && s.trim().length > 0;
}
/**
 * Resolve credentials from the vault, with a legacy settings fallback.
 * Returns `null` if we don't have enough to authenticate a request.
 */
function resolveCredentials() {
    const cfg = vscode.workspace.getConfiguration('founderOs');
    const settingsUrl = cfg.get('apiBaseUrl');
    const settingsNodeId = cfg.get('nodeId');
    const settingsNodeToken = cfg.get('nodeToken');
    const vault = readVaultConfig();
    const rawUrl = nonEmpty(settingsUrl) ? settingsUrl : vault?.apiBaseUrl ?? '';
    const apiBaseUrl = rawUrl ? normalizeApiBaseUrl(rawUrl) : '';
    const nodeId = vault?.nodeId
        ?? (nonEmpty(settingsNodeId) ? settingsNodeId : '');
    const nodeToken = vault?.nodeToken
        ?? (nonEmpty(settingsNodeToken) ? settingsNodeToken : '');
    if (!apiBaseUrl || !nodeId || !nodeToken)
        return null;
    const usedSettings = nonEmpty(settingsUrl)
        || (!vault && (nonEmpty(settingsNodeId) || nonEmpty(settingsNodeToken)));
    const usedVault = Boolean(vault);
    const source = usedSettings && usedVault ? 'mixed' : usedSettings ? 'settings' : 'vault';
    return { apiBaseUrl, nodeId, nodeToken, source };
}
/**
 * Normalize vault / marketing host to the API origin used by the gateway.
 *
 * Production serves the Nest API on the apex host (`https://doxxedcrypto.digital/api/...`).
 * Do **not** rewrite to `api.doxxedcrypto.digital` — that hostname has no DNS record.
 */
function normalizeApiBaseUrl(url) {
    const trimmed = url.replace(/\/$/, '');
    try {
        const u = new URL(trimmed);
        if (u.hostname === 'www.doxxedcrypto.digital') {
            u.hostname = 'doxxedcrypto.digital';
            return u.origin;
        }
        if (u.hostname === 'api.doxxedcrypto.digital') {
            // Legacy / mistaken settings — map back to the live apex API.
            u.hostname = 'doxxedcrypto.digital';
            return u.origin;
        }
    }
    catch {
        // fall through
    }
    return trimmed;
}
/**
 * Synchronize non-secret connection metadata and remove credentials written by
 * older builds. The vault remains the single local source of the node token.
 */
async function syncVaultIntoSettings() {
    const vault = readVaultConfig();
    if (!vault)
        return null;
    const apiBaseUrl = normalizeApiBaseUrl(vault.apiBaseUrl);
    const cfg = vscode.workspace.getConfiguration('founderOs');
    try {
        await cfg.update('apiBaseUrl', apiBaseUrl, vscode.ConfigurationTarget.Global);
        await cfg.update('nodeId', vault.nodeId, vscode.ConfigurationTarget.Global);
        const legacyToken = cfg.get('nodeToken');
        if (legacyToken === vault.nodeToken || legacyToken?.startsWith('fn_')) {
            await cfg.update('nodeToken', undefined, vscode.ConfigurationTarget.Global);
        }
    }
    catch {
        // Settings writes can fail in restricted hosts. The vault remains authoritative.
    }
    // Older installers copied the Founder bearer into provider settings. The
    // native Founder route now reads the vault directly, so remove those copies.
    try {
        const root = vscode.workspace.getConfiguration();
        for (const key of ['openAICompatible.apiKey', 'openai.apiKey']) {
            const value = root.get(key);
            if (value?.startsWith('fos_')) {
                await root.update(key, undefined, vscode.ConfigurationTarget.Global);
            }
        }
    }
    catch {
        // Best-effort migration. No new secret is written to settings.
    }
    return {
        apiBaseUrl,
        nodeId: vault.nodeId,
        nodeToken: vault.nodeToken,
        source: 'vault',
    };
}
/** Builds the OpenAI-compat proxy base URL: `{apiBaseUrl}/api/v1`. */
function proxyBaseUrl(apiBaseUrl) {
    return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}
/** OpenAI-compat apiKey value — clients send `Authorization: Bearer <this>`. */
function bearerFromCredentials(creds) {
    return `fos_${creds.nodeId}:${creds.nodeToken}`;
}
/** Full Authorization header for the Nest FounderNodeGuard (preferred). */
function authorizationHeaderFromCredentials(creds) {
    return `FounderNode ${creds.nodeId}:${creds.nodeToken}`;
}
/** True if the vault file is present on disk (regardless of whether it parses). */
function vaultFileExists() {
    try {
        return fs.existsSync(nodeConfigPath());
    }
    catch {
        return false;
    }
}
/**
 * Remove the local Founder session while preserving unrelated local runtime
 * preferences that may share node-config.json.
 */
async function clearFounderSession() {
    const file = nodeConfigPath();
    try {
        if (fs.existsSync(file)) {
            const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
            const credentialKeys = [
                'apiBaseUrl',
                'nodeId',
                'nodeToken',
                'founderId',
                'installId',
                'ipcSecret',
                'pairedAt',
                'tokenExpiresAt',
                'tokenRotatedAt',
            ];
            for (const key of credentialKeys)
                delete parsed[key];
            const meaningfulKeys = Object.keys(parsed).filter((key) => key !== 'version' && key !== 'label');
            if (meaningfulKeys.length === 0) {
                fs.unlinkSync(file);
            }
            else {
                fs.writeFileSync(file, JSON.stringify(parsed, null, 2), 'utf8');
            }
        }
    }
    catch {
        // Local sign-out continues even if the optional vault cleanup fails.
    }
    const cfg = vscode.workspace.getConfiguration('founderOs');
    for (const key of ['apiBaseUrl', 'nodeId', 'nodeToken']) {
        try {
            await cfg.update(key, undefined, vscode.ConfigurationTarget.Global);
        }
        catch {
            // Restricted hosts can reject settings writes. The vault is authoritative.
        }
    }
    try {
        const root = vscode.workspace.getConfiguration();
        for (const [key, url] of [
            ['openAICompatible.apiKey', 'openAICompatible.apiUrl'],
            ['openai.apiKey', 'openai.apiBase'],
        ]) {
            const currentKey = root.get(key);
            if (currentKey?.startsWith('fos_')) {
                await root.update(key, undefined, vscode.ConfigurationTarget.Global);
                await root.update(url, undefined, vscode.ConfigurationTarget.Global);
            }
        }
    }
    catch {
        // Best-effort cleanup for the compatibility provider.
    }
}
//# sourceMappingURL=credentials.js.map
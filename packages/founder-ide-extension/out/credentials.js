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
/**
 * Credential discovery for the Founder OS chat provider.
 *
 * Resolution order (first non-empty wins per field):
 *   1. `founderOs.apiBaseUrl` / `founderOs.nodeId` / `founderOs.nodeToken` settings
 *   2. `~/FounderVault/node-config.json`  (written by Founder Node on pair)
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
 * Resolve credentials from settings first, then the vault file.
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
    const nodeId = nonEmpty(settingsNodeId) ? settingsNodeId : vault?.nodeId ?? '';
    const nodeToken = nonEmpty(settingsNodeToken) ? settingsNodeToken : vault?.nodeToken ?? '';
    if (!apiBaseUrl || !nodeId || !nodeToken)
        return null;
    const usedSettings = nonEmpty(settingsUrl) || nonEmpty(settingsNodeId) || nonEmpty(settingsNodeToken);
    const usedVault = vault !== null;
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
 * Copy vault credentials into `founderOs.*` (+ OpenAI-compat) User settings so
 * pairing survives vault moves and OpenAI-compatible providers can reuse the same
 * Node bearer without a pairing-code prompt. Best-effort.
 */
async function syncVaultIntoSettings() {
    const vault = readVaultConfig();
    if (!vault)
        return null;
    const apiBaseUrl = normalizeApiBaseUrl(vault.apiBaseUrl);
    const bearer = `fos_${vault.nodeId}:${vault.nodeToken}`;
    // Nest global prefix is `/api`, controller is `v1` → `/api/v1`.
    const openaiBase = proxyBaseUrl(apiBaseUrl);
    const cfg = vscode.workspace.getConfiguration('founderOs');
    try {
        await cfg.update('apiBaseUrl', apiBaseUrl, vscode.ConfigurationTarget.Global);
        await cfg.update('nodeId', vault.nodeId, vscode.ConfigurationTarget.Global);
        await cfg.update('nodeToken', vault.nodeToken, vscode.ConfigurationTarget.Global);
    }
    catch {
        // Settings write can fail in restricted / remote hosts — vault still works.
    }
    // Best-effort OpenAI-compat keys for the Founder OS gateway in Founder IDE.
    try {
        const root = vscode.workspace.getConfiguration();
        await root.update('openAICompatible.apiUrl', openaiBase, vscode.ConfigurationTarget.Global);
        await root.update('openAICompatible.apiKey', bearer, vscode.ConfigurationTarget.Global);
    }
    catch {
        // optional
    }
    return {
        apiBaseUrl,
        nodeId: vault.nodeId,
        nodeToken: vault.nodeToken,
        source: 'settings',
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
//# sourceMappingURL=credentials.js.map
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');

/** Sibling folder — never inside git repo. */
export function getSecretsVaultRoot() {
  return process.env.DCF_SECRETS_VAULT
    ? path.resolve(process.env.DCF_SECRETS_VAULT)
    : path.resolve(repoRoot, '..', 'doxedcryptofounder-secrets');
}

export function getVaultDir() {
  return path.join(getSecretsVaultRoot(), 'vault');
}

export function getAuditExportRoot() {
  return process.env.DCF_AUDIT_EXPORT
    ? path.resolve(process.env.DCF_AUDIT_EXPORT)
    : path.resolve(repoRoot, '..', 'doxedcryptofounder-audit');
}

export { repoRoot };

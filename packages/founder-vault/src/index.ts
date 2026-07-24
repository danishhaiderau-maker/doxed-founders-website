export * from './paths.js';
export * from './schema.js';
export * from './snapshot.js';
export * from './crypto.js';
export * from './vector-index.js';
export * from './vault-apply.js';
export * from './local-agents.js';
export * from './vault-merge-apply.js';
// Phase 3 contract — Workstream C (status endpoint) and Workstream B (IDE IPC
// consumer) read this through the package surface. Re-exported here so callers
// don't need a subpath import; subpath also exposed in package.json `exports`.
export * from './status-schema.js';

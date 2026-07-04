import pkg from '../package.json';

/** Canonical app semver — must match apps/founder-node/package.json `version`. */
export const FOUNDER_NODE_APP_VERSION = pkg.version;

/** Tray / heartbeat label (same as app version). */
export const FOUNDER_NODE_LOCAL_VERSION = FOUNDER_NODE_APP_VERSION;

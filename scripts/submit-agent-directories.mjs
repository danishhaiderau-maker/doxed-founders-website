#!/usr/bin/env node
/** @deprecated Use scripts/register-agents-automated.mjs */
console.warn('submit-agent-directories is deprecated → npm run register:agents-automated\n');
await import('./register-agents-automated.mjs');

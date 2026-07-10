#!/usr/bin/env node
/**
 * Verify (and optionally restore) pinned combo_pathway_config.py tile stack.
 *
 *   node scripts/ensure-combo-pathway-pin.mjs           # restore if drifted
 *   node scripts/ensure-combo-pathway-pin.mjs --check   # exit 2 on drift, no write
 */
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ensureComboPathwayPinned, inspectComboPathwayConfig } from './lib/combo-pathway-pin.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const checkOnly = process.argv.includes('--check');

if (checkOnly) {
  const inspection = inspectComboPathwayConfig(ROOT);
  if (inspection.ok) {
    console.log(`OK combo_pathway_config ${inspection.version}`);
    process.exit(0);
  }
  console.error(
    `DRIFT combo_pathway_config (${inspection.reason}) version=${inspection.version} ` +
      `missing=${(inspection.missing || []).join(',') || '-'} ` +
      `forbidden=${(inspection.forbidden || []).join(',') || '-'}`,
  );
  console.error('Run: node scripts/ensure-combo-pathway-pin.mjs');
  process.exit(2);
}

const result = ensureComboPathwayPinned(ROOT, { restore: true });
console.log(result.message);
if (!result.inspection.ok) process.exit(1);
process.exit(0);

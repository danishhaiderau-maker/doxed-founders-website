/**
 * Guard combo_pathway_config.py against silent revert to git HEAD (AI60/v11.2).
 * Pin source: config/pins/combo_pathway_config.pin.json + pinned .py snapshot.
 */
import { readFileSync, writeFileSync, copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';

const PIN_REL = 'config/pins/combo_pathway_config.pin.json';

export function loadComboPathwayPin(root) {
  const pinPath = join(root, PIN_REL);
  if (!existsSync(pinPath)) return null;
  return { ...JSON.parse(readFileSync(pinPath, 'utf8')), pinPath };
}

export function inspectComboPathwayConfig(root, pin = loadComboPathwayPin(root)) {
  if (!pin) {
    return { ok: false, reason: 'missing_pin_manifest', pin: null, targetPath: null };
  }
  const targetPath = join(root, pin.targetFile);
  if (!existsSync(targetPath)) {
    return { ok: false, reason: 'missing_target', pin, targetPath, text: '' };
  }
  const text = readFileSync(targetPath, 'utf8');
  const missing = (pin.requiredMarkers || []).filter((m) => !text.includes(m));
  const forbidden = (pin.forbiddenMarkers || []).filter((m) => text.includes(m));
  const versionMatch = text.match(/RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"/);
  const version = versionMatch?.[1] ?? null;
  const ok =
    missing.length === 0 &&
    forbidden.length === 0 &&
    (!pin.expectedStackVersion || version === pin.expectedStackVersion);
  return {
    ok,
    reason: ok ? 'ok' : missing.length ? 'missing_markers' : forbidden.length ? 'forbidden_markers' : 'version_mismatch',
    pin,
    targetPath,
    version,
    missing,
    forbidden,
    text,
  };
}

/**
 * Restore target from pinned snapshot when autoRestore is enabled.
 * @returns {{ restored: boolean, inspection: object, message: string }}
 */
export function ensureComboPathwayPinned(root, { restore = true, force = false } = {}) {
  const pin = loadComboPathwayPin(root);
  const inspection = inspectComboPathwayConfig(root, pin);
  if (!pin) {
    return { restored: false, inspection, message: 'No combo_pathway_config pin manifest.' };
  }
  if (inspection.ok) {
    return {
      restored: false,
      inspection,
      message: `combo_pathway_config OK (${inspection.version})`,
    };
  }
  if (!restore || (pin.autoRestore === false && !force)) {
    return {
      restored: false,
      inspection,
      message:
        `combo_pathway_config DRIFT (${inspection.reason}): version=${inspection.version}; ` +
        `missing=${(inspection.missing || []).join(',') || '-'}; ` +
        `forbidden=${(inspection.forbidden || []).join(',') || '-'}`,
    };
  }
  const pinSrc = join(root, pin.pinFile);
  if (!existsSync(pinSrc)) {
    return {
      restored: false,
      inspection,
      message: `Pin snapshot missing: ${pin.pinFile}`,
    };
  }
  mkdirSync(dirname(inspection.targetPath), { recursive: true });
  // Keep a .bak of whatever was wrong so agents can inspect the overwrite source.
  if (existsSync(inspection.targetPath)) {
    const bak = `${inspection.targetPath}.bak-pre-pin-restore-${new Date()
      .toISOString()
      .replace(/[:.]/g, '')
      .slice(0, 15)}`;
    try {
      copyFileSync(inspection.targetPath, bak);
    } catch {
      /* ignore */
    }
  }
  writeFileSync(inspection.targetPath, readFileSync(pinSrc), 'utf8');
  const after = inspectComboPathwayConfig(root, pin);
  return {
    restored: after.ok,
    inspection: after,
    message: after.ok
      ? `RESTORED combo_pathway_config from pin → ${after.version}`
      : `RESTORE FAILED — pin snapshot itself invalid (${after.reason})`,
  };
}

/** Refuse sync writers that would overwrite the pinned tile config. */
export function assertComboPathwaySyncAllowed(root, { forceEnv = 'COMBO_PATHWAY_SYNC_FORCE' } = {}) {
  const pin = loadComboPathwayPin(root);
  if (!pin) return { allowed: true };
  const forced = process.env[forceEnv] === '1' || process.env[forceEnv] === 'true';
  if (forced) {
    console.warn(
      `\n⚠  [combo-pathway-pin] ${forceEnv} set — allowing overwrite of pinned combo_pathway_config.py\n`,
    );
    return { allowed: true, forced: true };
  }
  console.error(
    [
      '',
      '══════════════════════════════════════════════════════════════════════',
      '  BLOCKED: combo_pathway_config.py is PINNED (v11.4 tile stack)',
      '══════════════════════════════════════════════════════════════════════',
      '',
      `  Expected: ${pin.expectedStackVersion}`,
      `  Pin:      ${pin.pinFile}`,
      '',
      '  Sync must NOT overwrite this file (wipes SL_AVOIDANCE_V1 / SIZED_CONTINUOUS_V1).',
      '  Edit services/btc-conservative-agent/combo_pathway_config.py in place, or',
      `  set ${forceEnv}=1 only after explicit human confirmation.`,
      '',
      '══════════════════════════════════════════════════════════════════════',
      '',
    ].join('\n'),
  );
  process.exit(1);
}

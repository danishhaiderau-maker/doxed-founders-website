#!/usr/bin/env node

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_REPO_ROOT = path.resolve(scriptDir, '..', '..', '..');
export const DEFAULT_RECEIPT_PATH = 'artifacts/releases/0.9.4/release-receipt.json';

const REQUIRED_VIEWPORTS = [
  [1440, 900],
  [1280, 720],
  [1187, 739],
  [768, 900],
];

const REQUIRED_NAVIGATION_CHECKS = [
  'founderVisible',
  'newChatVisible',
  'projectsVisible',
  'chatsVisible',
  'agentsVisible',
  'graphVisible',
];

const REQUIRED_SETTINGS_CHECKS = [
  'founderSettings',
  'personalAi',
  'bringYourOwnKey',
  'managedAliases',
  'customModel',
];

const REQUIRED_WORK_MODES = ['ask', 'plan', 'build', 'debug', 'team'];

const REQUIRED_VISUAL_INPUT_CHECKS = [
  'picker',
  'paste',
  'drop',
  'preview',
  'remove',
  'annotationAware',
  'nonBlank',
  'noOverflow',
  'noOverlap',
  'textFits',
];

const VISUAL_REVIEWER_KINDS = new Set([
  'managed-ai',
  'personal-ai',
  'local-ai',
  'human',
]);

const MAX_VISUAL_REVIEW_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_VISUAL_REVIEW_OUTPUT_BYTES = 128 * 1024;

const LIKELY_SECRET_PATTERN =
  /bearer\s+[a-z0-9._-]{12,}|"(?:apiKey|nodeToken|accessToken|refreshToken)"\s*:\s*"[^"]+"|sk-[a-z0-9]{8,}/i;

export function sha256File(file) {
  const hash = crypto.createHash('sha256');
  hash.update(fs.readFileSync(file));
  return hash.digest('hex').toUpperCase();
}

export function describeFile(repoRoot, relativePath) {
  const file = resolveInside(repoRoot, relativePath);
  const stat = fs.statSync(file);
  return {
    path: normalizeRelativePath(relativePath),
    bytes: stat.size,
    sha256: sha256File(file),
  };
}

function normalizeRelativePath(relativePath) {
  return relativePath.replaceAll('\\', '/');
}

function resolveInside(repoRoot, relativePath) {
  assert.equal(path.isAbsolute(relativePath), false, `evidence path must be relative: ${relativePath}`);
  const root = path.resolve(repoRoot);
  const resolved = path.resolve(root, relativePath);
  const rootPrefix = `${root}${path.sep}`.toLowerCase();
  assert.ok(
    resolved.toLowerCase().startsWith(rootPrefix),
    `evidence path leaves the repository: ${relativePath}`,
  );
  return resolved;
}

function readJson(file, label) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${error.message}`);
  }
}

function arrayIsEmpty(value) {
  return Array.isArray(value) && value.length === 0;
}

function requireNoCriticalErrors(evidence, label) {
  assert.ok(
    arrayIsEmpty(evidence.criticalConsoleErrors ?? []),
    `${label} contains critical console errors`,
  );
  assert.ok(arrayIsEmpty(evidence.pageErrors ?? []), `${label} contains page errors`);
}

function verifyRecordedFile(repoRoot, recorded, label) {
  assert.ok(recorded && typeof recorded === 'object', `${label} file record is missing`);
  const file = resolveInside(repoRoot, recorded.path);
  assert.ok(fs.existsSync(file), `${label} is missing: ${recorded.path}`);
  const stat = fs.statSync(file);
  assert.ok(stat.isFile(), `${label} is not a file: ${recorded.path}`);
  assert.equal(stat.size, recorded.bytes, `${label} byte length changed`);
  assert.match(recorded.sha256, /^[A-F0-9]{64}$/, `${label} SHA-256 is malformed`);
  assert.equal(sha256File(file), recorded.sha256, `${label} SHA-256 changed`);
  return file;
}

function requireIsoTimestamp(value, label) {
  assert.equal(typeof value, 'string', `${label} timestamp is missing`);
  assert.ok(
    Number.isFinite(Date.parse(value)),
    `${label} timestamp is invalid`,
  );
}

function verifyVisualReview({
  repoRoot,
  receipt,
  evidenceFiles,
  check,
}) {
  const visualReceipt = receipt.visualReview;
  check(
    visualReceipt && typeof visualReceipt === 'object',
    'visual review receipt is missing',
  );

  const evidenceRecord = evidenceFiles.get(visualReceipt.evidencePath);
  const annotatedInputRecord = evidenceFiles.get(visualReceipt.annotatedInputPath);
  check(Boolean(evidenceRecord), 'visual review JSON is not in the evidence file manifest');
  check(
    Boolean(annotatedInputRecord),
    'annotated visual input is not in the evidence file manifest',
  );

  const visual = readJson(
    resolveInside(repoRoot, evidenceRecord.path),
    'visual review evidence',
  );
  check(visual.schemaVersion === 1, 'visual review evidence schemaVersion must be 1');
  check(visual.redacted === true, 'visual review evidence is not marked redacted');
  check(
    Number.isInteger(visual.inputBytes)
      && visual.inputBytes > 0
      && visual.inputBytes <= MAX_VISUAL_REVIEW_INPUT_BYTES,
    'visual review input byte count is missing or outside its bound',
  );
  check(
    Number.isInteger(visual.outputBytes)
      && visual.outputBytes > 0
      && visual.outputBytes <= MAX_VISUAL_REVIEW_OUTPUT_BYTES,
    'visual review output byte count is missing or outside its bound',
  );

  for (const visualCheck of REQUIRED_VISUAL_INPUT_CHECKS) {
    check(
      visual.checks?.[visualCheck] === true,
      `visual review failed check ${visualCheck}`,
    );
  }

  for (const mode of REQUIRED_WORK_MODES) {
    const modeReceipt = visualReceipt.modeScreenshots?.[mode];
    const modeScreenshot = evidenceFiles.get(modeReceipt);
    check(Boolean(modeScreenshot), `${mode} visual screenshot is not in the evidence file manifest`);
    const modeEvidence = visual.modes?.[mode];
    check(
      modeEvidence?.attachmentSubmitted === true,
      `${mode} did not submit the visual attachment`,
    );
    check(
      modeEvidence?.visualContextPresent === true,
      `${mode} did not inject bounded visual context`,
    );
    check(
      modeEvidence?.responseVisible === true,
      `${mode} has no visible final response`,
    );
    check(modeEvidence?.errorVisible === false, `${mode} shows a visual-input error`);
  }

  const reviewer = visual.reviewer;
  check(
    reviewer && VISUAL_REVIEWER_KINDS.has(reviewer.kind),
    'visual reviewer kind is missing or unsupported',
  );
  check(reviewer.approved === true, 'visual reviewer did not approve the rendered result');
  requireIsoTimestamp(reviewer.reviewedAt, 'visual review');

  if (reviewer.kind === 'human') {
    check(
      typeof reviewer.signedBy === 'string' && reviewer.signedBy.trim().length >= 2,
      'human visual sign-off identity is missing',
    );
  } else {
    check(
      typeof reviewer.profile === 'string' && reviewer.profile.trim().length > 0,
      'AI visual reviewer profile is missing',
    );
    check(
      typeof reviewer.model === 'string' && reviewer.model.trim().length > 0,
      'AI visual reviewer model is missing',
    );
    check(
      typeof reviewer.route === 'string' && reviewer.route.trim().length > 0,
      'AI visual reviewer route is missing',
    );
    check(
      typeof reviewer.resultSha256 === 'string'
        && /^[A-F0-9]{64}$/.test(reviewer.resultSha256),
      'AI visual review result hash is missing or malformed',
    );
  }

  requireNoCriticalErrors(visual, 'visual review evidence');
}

export function getAuthenticodeStatus(artifactPath) {
  if (process.platform !== 'win32') {
    throw new Error('Authenticode verification requires Windows');
  }
  const result = spawnSync(
    'powershell.exe',
    [
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      '(Get-AuthenticodeSignature -LiteralPath $env:FOUNDER_RELEASE_ARTIFACT).Status.ToString()',
    ],
    {
      encoding: 'utf8',
      env: { ...process.env, FOUNDER_RELEASE_ARTIFACT: artifactPath },
      windowsHide: true,
    },
  );
  if (result.status !== 0) {
    throw new Error(`Authenticode verification failed: ${(result.stderr || result.stdout).trim()}`);
  }
  return result.stdout.trim();
}

function releaseSection(markdown, version) {
  const escaped = version.replaceAll('.', '\\.');
  const heading = new RegExp(`^## ${escaped}\\b`, 'm').exec(markdown);
  assert.ok(heading, `RELEASES.md has no ${version} section`);
  const remainder = markdown.slice(heading.index);
  const nextHeading = /^## /m.exec(remainder.slice(heading[0].length));
  return nextHeading
    ? remainder.slice(0, heading[0].length + nextHeading.index)
    : remainder;
}

export function validateReleaseReceipt({
  repoRoot = DEFAULT_REPO_ROOT,
  receiptPath = DEFAULT_RECEIPT_PATH,
  signatureStatus,
} = {}) {
  const absoluteReceipt = resolveInside(repoRoot, receiptPath);
  const receipt = readJson(absoluteReceipt, 'release receipt');
  let checks = 0;
  const check = (condition, message) => {
    checks += 1;
    assert.ok(condition, message);
  };

  check(
    receipt.schemaVersion === 1 || receipt.schemaVersion === 2,
    'release receipt schemaVersion must be 1 or 2',
  );
  check(
    typeof receipt.releaseVersion === 'string'
      && /^\d+\.\d+\.\d+$/.test(receipt.releaseVersion),
    'release receipt releaseVersion must be semver',
  );
  check(receipt.channel === 'internal-test', 'unsigned release evidence must remain internal-test');
  check(
    receipt.artifact?.authenticodeStatus === 'NotSigned',
    'receipt must truthfully record Authenticode NotSigned',
  );

  const artifactPath = verifyRecordedFile(repoRoot, receipt.artifact, 'installer');
  checks += 5;
  const actualSignature = signatureStatus ?? getAuthenticodeStatus(artifactPath);
  check(actualSignature === 'NotSigned', `installer Authenticode status is ${actualSignature}`);

  const evidenceFiles = new Map();
  const bundleDirectory = normalizeRelativePath(receipt.bundleDirectory ?? '');
  check(Boolean(bundleDirectory), 'release receipt bundleDirectory is missing');
  for (const fileRecord of receipt.files ?? []) {
    check(
      normalizeRelativePath(fileRecord.path).startsWith(`${bundleDirectory}/`),
      `evidence is outside the canonical bundle: ${fileRecord.path}`,
    );
    check(!evidenceFiles.has(fileRecord.path), `duplicate evidence path: ${fileRecord.path}`);
    const evidenceFile = verifyRecordedFile(
      repoRoot,
      fileRecord,
      `evidence ${fileRecord.path}`,
    );
    checks += 5;
    if (fileRecord.path.endsWith('.json')) {
      check(
        !LIKELY_SECRET_PATTERN.test(fs.readFileSync(evidenceFile, 'utf8')),
        `evidence contains a likely credential: ${fileRecord.path}`,
      );
    }
    evidenceFiles.set(fileRecord.path, fileRecord);
  }
  const absoluteBundleDirectory = resolveInside(repoRoot, bundleDirectory);
  const canonicalFiles = fs.readdirSync(absoluteBundleDirectory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(?:json|png)$/i.test(entry.name))
    .map((entry) => normalizeRelativePath(path.join(bundleDirectory, entry.name)))
    .filter((relativePath) => relativePath !== normalizeRelativePath(receiptPath))
    .sort();
  check(
    JSON.stringify(canonicalFiles) === JSON.stringify([...evidenceFiles.keys()].sort()),
    'canonical bundle has unlisted or missing JSON/PNG evidence',
  );

  const chatRecord = evidenceFiles.get(receipt.chat?.evidencePath);
  const chatScreenshot = evidenceFiles.get(receipt.chat?.screenshotPath);
  check(Boolean(chatRecord), 'managed chat JSON is not in the evidence file manifest');
  check(Boolean(chatScreenshot), 'managed chat screenshot is not in the evidence file manifest');
  const chat = readJson(resolveInside(repoRoot, chatRecord.path), 'managed chat evidence');
  check(chat.nonce === receipt.chat.nonce, 'managed chat nonce does not match the receipt');
  check(chat.checks?.submitted === true, 'managed chat was not submitted');
  check(chat.checks?.responseVisible === true, 'managed chat has no visible response');
  check(chat.checks?.founderRoute === true, 'managed chat has no Founder route receipt');
  check(chat.checks?.deepSeekV4 === true, 'managed chat did not prove DeepSeek V4');
  check(chat.checks?.errorVisible === false, 'managed chat shows an error');
  check(
    receipt.chat.routeIncludes.every((part) => chat.route?.includes(part)),
    'managed chat route does not match the documented route',
  );
  requireNoCriticalErrors(chat, 'managed chat evidence');
  checks += 2;

  const viewportKeys = new Set();
  for (const viewport of receipt.viewports ?? []) {
    const key = `${viewport.width}x${viewport.height}`;
    viewportKeys.add(key);
    const jsonRecord = evidenceFiles.get(viewport.evidencePath);
    const screenshotRecord = evidenceFiles.get(viewport.screenshotPath);
    check(Boolean(jsonRecord), `${key} viewport JSON is not in the evidence file manifest`);
    check(Boolean(screenshotRecord), `${key} screenshot is not in the evidence file manifest`);
    const evidence = readJson(resolveInside(repoRoot, jsonRecord.path), `${key} evidence`);
    check(evidence.viewport?.innerWidth === viewport.width, `${key} width does not match`);
    check(evidence.viewport?.innerHeight === viewport.height, `${key} height does not match`);
    for (const navigationCheck of REQUIRED_NAVIGATION_CHECKS) {
      check(
        evidence.checks?.[navigationCheck] === true,
        `${key} failed navigation check ${navigationCheck}`,
      );
    }
    check(evidence.checks?.voidSettingsVisible === false, `${key} shows Void settings`);
    requireNoCriticalErrors(evidence, `${key} evidence`);
    checks += 2;
  }
  for (const [width, height] of REQUIRED_VIEWPORTS) {
    check(viewportKeys.has(`${width}x${height}`), `required viewport ${width}x${height} is missing`);
  }

  const settingsRecord = evidenceFiles.get(receipt.settings?.evidencePath);
  const settingsScreenshot = evidenceFiles.get(receipt.settings?.screenshotPath);
  check(Boolean(settingsRecord), 'settings JSON is not in the evidence file manifest');
  check(Boolean(settingsScreenshot), 'settings screenshot is not in the evidence file manifest');
  const settings = readJson(resolveInside(repoRoot, settingsRecord.path), 'settings evidence');
  for (const settingsCheck of REQUIRED_SETTINGS_CHECKS) {
    check(settings.checks?.[settingsCheck] === true, `settings failed check ${settingsCheck}`);
  }
  check(settings.checks?.voidSettings === false, 'settings evidence contains Void branding');

  if (receipt.schemaVersion === 1) {
    check(
      receipt.releaseVersion === '0.9.4' && receipt.channel === 'internal-test',
      'release schemaVersion 1 is historical evidence only; final candidates require schemaVersion 2',
    );
  } else {
    verifyVisualReview({
      repoRoot,
      receipt,
      evidenceFiles,
      check,
    });
  }

  const releasesPath = resolveInside(repoRoot, receipt.documentation.releaseNotesPath);
  const releaseNotes = releaseSection(
    fs.readFileSync(releasesPath, 'utf8'),
    receipt.releaseVersion,
  );
  check(
    releaseNotes.includes(receipt.artifact.sha256),
    'RELEASES.md does not contain the installer SHA-256',
  );
  check(
    releaseNotes.includes(receipt.artifact.bytes.toLocaleString('en-US')),
    'RELEASES.md does not contain the installer byte length',
  );
  check(
    releaseNotes.includes(receipt.chat.nonce),
    'RELEASES.md does not contain the managed chat nonce',
  );
  check(
    /unsigned[\s\S]{0,80}internal testing/i.test(releaseNotes),
    'RELEASES.md does not restrict the unsigned artifact to internal testing',
  );
  check(
    releaseNotes.includes(normalizeRelativePath(receiptPath)),
    'RELEASES.md does not link the canonical release receipt',
  );

  return {
    checks,
    receipt,
    signatureStatus: actualSignature,
  };
}

function runCli() {
  const receiptPath = process.argv[2] ?? DEFAULT_RECEIPT_PATH;
  try {
    const result = validateReleaseReceipt({ receiptPath });
    console.log(
      `Founder IDE ${result.receipt.releaseVersion} release evidence: `
      + `${result.checks} checks passed; Authenticode=${result.signatureStatus}; `
      + `channel=${result.receipt.channel}`,
    );
  } catch (error) {
    console.error(`Founder IDE release evidence failed: ${error.message}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runCli();
}

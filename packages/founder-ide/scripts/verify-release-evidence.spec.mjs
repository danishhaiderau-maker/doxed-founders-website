import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
  describeFile,
  validateReleaseReceipt,
} from './verify-release-evidence.mjs';

const sha = (contents) =>
  crypto.createHash('sha256').update(contents).digest('hex').toUpperCase();

function writeJson(root, relativePath, value) {
  const file = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function buildFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-release-evidence-'));
  const version = '0.9.4';
  const nonce = 'V1-CORRECTED-20260723-1848';
  const artifactContents = Buffer.from('internal installer fixture');
  fs.writeFileSync(path.join(root, 'installer.exe'), artifactContents);

  const evidencePaths = [];
  for (const [width, height] of [
    [1440, 900],
    [1280, 720],
    [1187, 739],
    [768, 900],
  ]) {
    const jsonPath = `evidence/evidence-${width}x${height}.json`;
    const screenshotPath = `evidence/workbench-${width}x${height}.png`;
    writeJson(root, jsonPath, {
      viewport: { innerWidth: width, innerHeight: height },
      checks: {
        founderVisible: true,
        voidSettingsVisible: false,
        newChatVisible: true,
        projectsVisible: true,
        chatsVisible: true,
        agentsVisible: true,
        graphVisible: true,
      },
      criticalConsoleErrors: [],
      pageErrors: [],
    });
    fs.writeFileSync(path.join(root, screenshotPath), Buffer.from(`png ${width}x${height}`));
    evidencePaths.push(jsonPath, screenshotPath);
  }

  writeJson(root, 'evidence/chat.json', {
    nonce,
    route: 'reasoning · deepseek/deepseek-v4-pro · 1908 ms',
    checks: {
      submitted: true,
      responseVisible: true,
      founderRoute: true,
      deepSeekV4: true,
      errorVisible: false,
    },
    criticalConsoleErrors: [],
    pageErrors: [],
  });
  fs.writeFileSync(path.join(root, 'evidence/chat.png'), Buffer.from('chat png'));
  writeJson(root, 'evidence/settings.json', {
    checks: {
      founderSettings: true,
      voidSettings: false,
      personalAi: true,
      bringYourOwnKey: true,
      managedAliases: true,
      customModel: true,
    },
  });
  fs.writeFileSync(path.join(root, 'evidence/settings.png'), Buffer.from('settings png'));
  evidencePaths.push(
    'evidence/chat.json',
    'evidence/chat.png',
    'evidence/settings.json',
    'evidence/settings.png',
  );

  const artifactHash = sha(artifactContents);
  fs.writeFileSync(
    path.join(root, 'RELEASES.md'),
    [
      '# Releases',
      '',
      `## ${version} - INTERNAL TEST CANDIDATE`,
      '',
      `Artifact is ${artifactContents.length.toLocaleString('en-US')} bytes.`,
      `SHA-256 ${artifactHash}.`,
      `Nonce ${nonce}.`,
      'This unsigned build is restricted to internal testing.',
      'Receipt: artifacts/releases/0.9.4/release-receipt.json',
      '',
      '## 0.9.3',
    ].join('\n'),
    'utf8',
  );

  const receipt = {
    schemaVersion: 1,
    releaseVersion: version,
    channel: 'internal-test',
    bundleDirectory: 'evidence',
    artifact: {
      ...describeFile(root, 'installer.exe'),
      authenticodeStatus: 'NotSigned',
    },
    files: evidencePaths.map((relativePath) => describeFile(root, relativePath)),
    chat: {
      nonce,
      evidencePath: 'evidence/chat.json',
      screenshotPath: 'evidence/chat.png',
      routeIncludes: ['reasoning', 'deepseek/deepseek-v4-pro'],
    },
    viewports: [
      [1440, 900],
      [1280, 720],
      [1187, 739],
      [768, 900],
    ].map(([width, height]) => ({
      width,
      height,
      evidencePath: `evidence/evidence-${width}x${height}.json`,
      screenshotPath: `evidence/workbench-${width}x${height}.png`,
    })),
    settings: {
      evidencePath: 'evidence/settings.json',
      screenshotPath: 'evidence/settings.png',
    },
    documentation: {
      releaseNotesPath: 'RELEASES.md',
    },
  };
  writeJson(root, 'artifacts/releases/0.9.4/release-receipt.json', receipt);
  return { root, receipt };
}

describe('Founder IDE release evidence verifier', () => {
  it('accepts one complete, unsigned internal evidence bundle', () => {
    const { root } = buildFixture();
    const result = validateReleaseReceipt({
      repoRoot: root,
      signatureStatus: 'NotSigned',
    });
    assert.equal(result.receipt.releaseVersion, '0.9.4');
    assert.ok(result.checks > 80);
  });

  it('rejects stale chat evidence without a visible Founder response', () => {
    const { root, receipt } = buildFixture();
    writeJson(root, receipt.chat.evidencePath, {
      nonce: receipt.chat.nonce,
      route: null,
      checks: {
        submitted: true,
        responseVisible: false,
        founderRoute: false,
        deepSeekV4: false,
        errorVisible: true,
      },
      criticalConsoleErrors: [],
      pageErrors: [],
    });
    receipt.files = receipt.files.map((record) =>
      record.path === receipt.chat.evidencePath
        ? describeFile(root, receipt.chat.evidencePath)
        : record);
    writeJson(root, 'artifacts/releases/0.9.4/release-receipt.json', receipt);
    assert.throws(
      () => validateReleaseReceipt({ repoRoot: root, signatureStatus: 'NotSigned' }),
      /visible response/,
    );
  });

  it('rejects release notes that do not name the verified nonce', () => {
    const { root, receipt } = buildFixture();
    const releases = fs.readFileSync(path.join(root, 'RELEASES.md'), 'utf8');
    fs.writeFileSync(
      path.join(root, 'RELEASES.md'),
      releases.replace(receipt.chat.nonce, 'STALE-NONCE'),
      'utf8',
    );
    assert.throws(
      () => validateReleaseReceipt({ repoRoot: root, signatureStatus: 'NotSigned' }),
      /managed chat nonce/,
    );
  });

  it('rejects likely credentials in curated JSON evidence', () => {
    const { root, receipt } = buildFixture();
    const chat = JSON.parse(
      fs.readFileSync(path.join(root, receipt.chat.evidencePath), 'utf8'),
    );
    chat.apiKey = 'sk-this-must-never-enter-release-evidence';
    writeJson(root, receipt.chat.evidencePath, chat);
    receipt.files = receipt.files.map((record) =>
      record.path === receipt.chat.evidencePath
        ? describeFile(root, receipt.chat.evidencePath)
        : record);
    writeJson(root, 'artifacts/releases/0.9.4/release-receipt.json', receipt);
    assert.throws(
      () => validateReleaseReceipt({ repoRoot: root, signatureStatus: 'NotSigned' }),
      /likely credential/,
    );
  });

  it('rejects stale JSON that is not listed in the canonical receipt', () => {
    const { root } = buildFixture();
    writeJson(root, 'evidence/stale-failed-run.json', { failed: true });
    assert.throws(
      () => validateReleaseReceipt({ repoRoot: root, signatureStatus: 'NotSigned' }),
      /unlisted or missing/,
    );
  });
});
